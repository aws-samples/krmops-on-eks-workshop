#!/bin/bash

# Script to monitor when a dbwebstack resource reaches ACTIVE state
# Usage: ./monitor_resource.sh <resource-name>

RESOURCE_NAME=${1:-webapp-instance}
START_TIME=$(date +%s)
INTERVAL=5  # Check every 5 seconds
MAX_WAIT=3600  # Maximum wait time in seconds (1 hour)

echo "Starting to monitor $RESOURCE_NAME at $(date)"
echo "Will check every $INTERVAL seconds until resource is ACTIVE"

while true; do
    # Get the current state of the resource
    RESULT=$(kubectl get dbwebstack $RESOURCE_NAME -o custom-columns=NAME:.metadata.name,STATE:.status.state,SYNCED:.status.synced --no-headers)
    
    # Extract STATE value
    STATE=$(echo "$RESULT" | awk '{print $2}')
    SYNCED=$(echo "$RESULT" | awk '{print $3}')  # Still capturing for display purposes
    
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Current state: STATE=$STATE, SYNCED=$SYNCED, Elapsed time: ${ELAPSED}s"
    
    # Check if the desired state is reached (only checking STATE=ACTIVE)
    if [[ "$STATE" == "ACTIVE" ]]; then
        echo "=========================================================="
        echo "SUCCESS! Resource reached ACTIVE state after ${ELAPSED} seconds"
        echo "Final state: $RESULT"
        echo "Start time: $(date -d @$START_TIME +"%Y-%m-%d %H:%M:%S")"
        echo "End time: $(date -d @$CURRENT_TIME +"%Y-%m-%d %H:%M:%S")"
        echo "Total time to reach ACTIVE state: ${ELAPSED} seconds ($(echo "$ELAPSED/60" | bc -l | xargs printf "%.2f") minutes)"
        echo "=========================================================="
        exit 0
    fi
    
    # Check if we've exceeded the maximum wait time
    if [[ $ELAPSED -gt $MAX_WAIT ]]; then
        echo "ERROR: Maximum wait time of $MAX_WAIT seconds exceeded. Resource did not reach ACTIVE state."
        exit 1
    fi
    
    # Wait before checking again
    sleep $INTERVAL
done