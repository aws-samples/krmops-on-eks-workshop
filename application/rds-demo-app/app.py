import os
import json
import pymysql
import logging
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)

# Define the secret mount path and secret file for DB credentials
SECRET_MOUNT_PATH = '/mnt/secrets-store'
SECRET_FILE = os.path.join(SECRET_MOUNT_PATH, 'dbsecret')

# Load DB credentials from the mounted secret file
try:
    with open(SECRET_FILE, 'r') as f:
        secret_data = json.load(f)
    db_user = secret_data.get('username')
    db_password = secret_data.get('password')
    logging.info("Loaded DB credentials from secret file")
except Exception as e:
    logging.error(f"Failed to load DB secret: {e}")
    db_user = os.getenv('DB_USER', 'defaultUser')
    db_password = os.getenv('DB_PASSWORD', 'defaultPassword')

# Get DB host from environment variable (similar to how S3 bucket name was retrieved)
db_host = os.getenv('DB_HOST')
# Get DB name from environment variable, default to 'testdb' if not provided
db_name = os.getenv('DB_NAME', 'testdb')
db_port = 3306  # default MySQL port

def create_database_if_not_exists():
    """Connect without specifying a database and create the target database if it doesn't exist."""
    try:
        temp_conn = pymysql.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            port=db_port,
            cursorclass=pymysql.cursors.DictCursor
        )
        with temp_conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
        temp_conn.commit()
        temp_conn.close()
        logging.info(f"Database '{db_name}' ensured (created if it did not exist).")
    except Exception as ex:
        logging.error(f"Failed to create database '{db_name}': {ex}")

# Connect to the RDS instance using pymysql, with logic to create the DB if needed
try:
    connection = pymysql.connect(
        host=db_host,
        user=db_user,
        password=db_password,
        database=db_name,
        port=db_port,
        cursorclass=pymysql.cursors.DictCursor
    )
    logging.info(f"Successfully connected to RDS instance at {db_host} and database '{db_name}'")
except pymysql.err.OperationalError as e:
    # MySQL error code 1049: Unknown database
    if e.args[0] == 1049:
        logging.info(f"Database '{db_name}' does not exist. Attempting to create it...")
        create_database_if_not_exists()
        # Now reconnect
        try:
            connection = pymysql.connect(
                host=db_host,
                user=db_user,
                password=db_password,
                database=db_name,
                port=db_port,
                cursorclass=pymysql.cursors.DictCursor
            )
            logging.info(f"Successfully connected to newly created database '{db_name}'")
        except Exception as ex:
            logging.error(f"Error reconnecting to database '{db_name}': {ex}")
            connection = None
    else:
        logging.error(f"Error connecting to RDS: {e}")
        connection = None

# Ensure the 'items' table exists
if connection:
    try:
        with connection.cursor() as cursor:
            create_table_query = """
            CREATE TABLE IF NOT EXISTS items (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                content TEXT NOT NULL
            )
            """
            cursor.execute(create_table_query)
        connection.commit()
        logging.info("Table 'items' ensured in the database")
    except Exception as e:
        logging.error(f"Error ensuring table exists: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/items', methods=['GET'])
def get_items():
    if not connection:
        return jsonify({'error': 'No DB connection'}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM items")
            rows = cursor.fetchall()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/items', methods=['POST'])
def add_item():
    if not connection:
        return jsonify({'error': 'No DB connection'}), 500
    try:
        data = request.get_json()
        name = data.get('name')
        content = data.get('content')
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO items (name, content) VALUES (%s, %s)", (name, content))
        connection.commit()
        return jsonify({'message': 'Item added successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    if not connection:
        return jsonify({'status': 'unhealthy', 'message': 'No DB connection'}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return jsonify({'status': 'healthy', 'message': 'Successfully connected to RDS and table is accessible'}), 200
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'message': f'Error accessing DB: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)