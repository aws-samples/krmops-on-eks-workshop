#!/usr/bin/env python3
import argparse
import boto3
import yaml
import os
import sys
import json
from yaml.representer import SafeRepresenter

# ──────────────────────────────────────────────────────────────────────────────
# Custom YAML dumper with block‑literal support
# ──────────────────────────────────────────────────────────────────────────────

class LiteralString(str):
    """A str subclass that will always be emitted as a block literal."""
    pass

def literal_representer(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')

class CustomDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)

# Register representers on CustomDumper
CustomDumper.add_representer(LiteralString, literal_representer)
CustomDumper.add_multi_representer(str, SafeRepresenter.represent_str)

# ──────────────────────────────────────────────────────────────────────────────
# Logging helpers
# ──────────────────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[INFO] {msg}")

def fail(msg):
    print(f"[ERROR] {msg}")
    sys.exit(1)
# ──────────────────────────────────────────────────────────────────────────────
# AWS interactions
# ──────────────────────────────────────────────────────────────────────────────

def get_cluster_details(cluster_name: str, region: str):
    eks = boto3.client("eks", region_name=region)
    resp = eks.describe_cluster(name=cluster_name)
    cluster = resp.get("cluster", {})
    vpc_cfg = cluster.get("resourcesVpcConfig", {})
    vpc_id = vpc_cfg.get("vpcId")
    subnet_ids = vpc_cfg.get("subnetIds", [])
    if not vpc_id or not subnet_ids:
        raise Exception("Failed to retrieve VPC ID or subnet IDs")
    return vpc_id, subnet_ids


def get_vpc_cidr(vpc_id: str, region: str):
    ec2 = boto3.client("ec2", region_name=region)
    vpcs = ec2.describe_vpcs(VpcIds=[vpc_id]).get("Vpcs", [])
    if not vpcs:
        raise Exception("VPC not found")
    return vpcs[0].get("CidrBlock")


def get_private_subnets(subnet_ids: list, region: str):
    ec2 = boto3.client("ec2", region_name=region)
    subs = ec2.describe_subnets(SubnetIds=subnet_ids).get("Subnets", [])
    priv = [s["SubnetId"] for s in subs if not s.get("MapPublicIpOnLaunch", False)]
    if len(priv) < 2:
        raise Exception("Not enough private subnets found")
    return priv[:2]
# ──────────────────────────────────────────────────────────────────────────────
# YAML‑update functions
# ──────────────────────────────────────────────────────────────────────────────

def update_network_yaml(path: str, vpc_id: str, cidr: str, subnets: list):
    log(f"Updating network YAML: {path}")
    with open(path) as f:
        doc = yaml.safe_load(f)
    if doc.get("kind") != "ResourceGraphDefinition":
        fail(f"{path} is not a ResourceGraphDefinition")
    for r in doc["spec"].get("resources", []):
        if r.get("id") == "securityGroup":
            spec = r["template"]["spec"]
            spec["vpcID"] = vpc_id
            for rule in spec.get("ingressRules", []):
                for ipr in rule.get("ipRanges", []):
                    ipr["cidrIP"] = cidr
        elif r.get("id") == "subnetGroup":
            r["template"]["spec"]["subnetIDs"] = subnets
    with open(path, "w") as f:
        yaml.dump(doc, f, Dumper=CustomDumper, default_flow_style=False, sort_keys=False)
    log("  ✓ network YAML updated")


def update_identity_yaml(path: str, cluster_name: str):
    log(f"Updating identity YAML: {path}")
    with open(path) as f:
        doc = yaml.safe_load(f)
    if doc.get("kind") != "ResourceGraphDefinition":
        fail(f"{path} is not a ResourceGraphDefinition")
    found = False
    for r in doc["spec"].get("resources", []):
        spec = r.get("template", {}).get("spec")
        if not spec:
            continue
        if r.get("id") == "podidentityassociation":
            spec["clusterName"] = cluster_name
            found = True
        elif r.get("id") == "role" and "assumeRolePolicyDocument" in spec:
            orig = spec["assumeRolePolicyDocument"]
            if isinstance(orig, dict):
                js = json.dumps(orig, indent=2)
            else:
                try:
                    parsed = json.loads(orig)
                    js = json.dumps(parsed, indent=2)
                except Exception:
                    js = orig
            spec["assumeRolePolicyDocument"] = LiteralString(js)
    if not found:
        fail("No 'podidentityassociation' block found")
    with open(path, "w") as f:
        yaml.dump(doc, f, Dumper=CustomDumper, default_flow_style=False, sort_keys=False)
    log("  ✓ identity YAML updated")
def update_dbwebstack_yaml(path: str, repo_uri: str, tag: str = "rds-latest", region: str = None):
    log(f"Updating DbWebStack YAML: {path}")
    with open(path) as f:
        doc = yaml.safe_load(f)
    if doc.get("kind") != "DbWebStack":
        fail(f"{path} is not a DbWebStack resource")
    full_image = f"{repo_uri}:{tag}" 
    doc["spec"]["image"] = full_image
    # Update the region field if region is provided
    if region and "rds" in doc["spec"] and doc["spec"]["rds"].get("enabled", False):
        doc["spec"]["rds"]["awsRegion"] = region
    with open(path, "w") as f:
        yaml.dump(doc, f, Dumper=CustomDumper, default_flow_style=False, sort_keys=False)
    log("  ✓ DbWebStack YAML updated")


def update_webstack_yaml(path: str, repo_uri: str, tag: str, cluster: str = None):
    log(f"Updating WebStack YAML: {path}")
    with open(path) as f:
        doc = yaml.safe_load(f)
    if doc.get("kind") != "WebStack":
        fail(f"{path} is not a WebStack resource")
    full_image = f"{repo_uri}:{tag}"
    doc["spec"]["image"] = full_image
    # Update the clusterName field if cluster is provided
    if cluster:
        doc["spec"]["clusterName"] = cluster
    with open(path, "w") as f:
        yaml.dump(doc, f, Dumper=CustomDumper, default_flow_style=False, sort_keys=False)
    log("  ✓ WebStack YAML updated")
def update_webapp_ingress_yaml(path: str, ingress_class: str = "alb"):
    log(f"Updating WebApp ingress in YAML: {path}")
    with open(path) as f:
        doc = yaml.safe_load(f)
    if doc.get("kind") != "ResourceGraphDefinition":
        fail(f"{path} is not a ResourceGraphDefinition")
    updated = False
    for r in doc["spec"].get("resources", []):
        if r.get("id") == "ingress":
            spec = r.get("template", {}).get("spec")
            if spec is None:
                continue
            spec["ingressClassName"] = ingress_class
            updated = True
    if not updated:
        fail("No 'ingress' resource found in WebApp YAML")
    with open(path, "w") as f:
        yaml.dump(doc, f, Dumper=CustomDumper, default_flow_style=False, sort_keys=False)
    log("  ✓ WebApp ingress YAML updated")
# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Sync multiple KRO YAMLs with EKS and ECR info"
    )
    p.add_argument("network_yaml", help="Path to network YAML file")
    p.add_argument("identity_yaml", help="Path to identity YAML file")
    p.add_argument("db_yaml", help="Path to DbWebStack YAML file (instance.yaml)")
    p.add_argument("web_yaml", help="Path to WebStack YAML file")
    p.add_argument("webapp_yaml", help="Path to WebApp ResourceGraphDefinition YAML file")
    p.add_argument("--cluster", default="kro", help="EKS cluster name")
    p.add_argument("--region", required=True, help="AWS region")
    p.add_argument(
        "--ecr-repo-uri", default=os.environ.get("ECR_IMAGE_URI"),
        help="ECR repository URI (without tag); falls back to $ECR_IMAGE_URI"
    )
    p.add_argument(
        "--ecr-tag", default="rds-latest",
        help="Tag to append to the DbWebStack image URI"
    )
    p.add_argument(
        "--web-tag", default="web-latest",
        help="Tag to append to the WebStack image URI"
    )
    p.add_argument(
        "--ingress-class", default="alb",
        help="Value to set for ingressClassName in WebApp ingress"
    )
    args = p.parse_args()

    for f in (args.network_yaml, args.identity_yaml, args.db_yaml, args.web_yaml, args.webapp_yaml):
        if not os.path.exists(f):
            fail(f"File not found: {f}")

    if not args.ecr_repo_uri:
        fail("ECR repository URI must be provided via --ecr-repo-uri or ECR_IMAGE_URI env var")

    try:
        log(f"Fetching cluster '{args.cluster}' in {args.region}")
        vpc_id, all_subnets = get_cluster_details(args.cluster, args.region)
        cidr = get_vpc_cidr(vpc_id, args.region)
        priv_subs = get_private_subnets(all_subnets, args.region)

        update_network_yaml(args.network_yaml, vpc_id, cidr, priv_subs)
        update_identity_yaml(args.identity_yaml, args.cluster)
        update_dbwebstack_yaml(args.db_yaml, args.ecr_repo_uri, args.ecr_tag, args.region)
        update_webstack_yaml(args.web_yaml, args.ecr_repo_uri, args.web_tag, args.cluster)
        update_webapp_ingress_yaml(args.webapp_yaml, args.ingress_class)

        log("✅ All updates completed successfully.")
    except Exception as e:
        fail(str(e))

if __name__ == "__main__":
    main()
