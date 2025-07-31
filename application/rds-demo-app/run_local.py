import os
import sys
from local_config import mock_connection, real_database
from flask import Flask, request, jsonify, render_template

# Create Flask app
app = Flask(__name__)

# Use the mock connection
connection = mock_connection

# Default connection status (False = disconnected)
connection_status = False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/items', methods=['GET'])
def get_items():
    # If disconnected, return error
    if not connection_status:
        return jsonify({'error': 'Database connection unavailable'}), 503
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM items")
            rows = cursor.fetchall()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/items', methods=['POST'])
def add_item():
    # If disconnected, return error
    if not connection_status:
        return jsonify({'error': 'Database connection unavailable'}), 503
    
    try:
        data = request.get_json()
        name = data.get('name')
        content = data.get('content')
        
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO items (name, content) VALUES (%s, %s)", (name, content))
        
        return jsonify({'message': 'Item added successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
@app.route('/items/<int:item_id>', methods=['DELETE'])
def delete_item(item_id):
    # If disconnected, return error
    if not connection_status:
        return jsonify({'error': 'Database connection unavailable'}), 503
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM items WHERE id = %s", (item_id,))
        
        if cursor.rowcount > 0:
            return jsonify({'message': 'Item deleted successfully'}), 200
        else:
            return jsonify({'error': 'Item not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    # Return unhealthy if disconnected
    if not connection_status:
        return jsonify({
            'status': 'unhealthy', 
            'message': 'Database connection unavailable'
        }), 503
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return jsonify({
            'status': 'healthy', 
            'message': 'Successfully connected to RDS database'
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy', 
            'message': f'Error accessing DB: {str(e)}'
        }), 500

@app.route('/toggle-connection', methods=['POST'])
def toggle_connection():
    global connection_status
    
    # Get the desired state from the request
    data = request.get_json()
    if data and 'enabled' in data:
        connection_status = data['enabled']
    else:
        # Toggle if no specific state is provided
        connection_status = not connection_status
    
    return jsonify({
        'status': 'connected' if connection_status else 'disconnected',
        'enabled': connection_status
    })

@app.route('/connection-status', methods=['GET'])
def get_connection_status():
    return jsonify({
        'status': 'connected' if connection_status else 'disconnected',
        'enabled': connection_status
    })

@app.route('/db-status', methods=['GET'])
def db_status():
    """Return detailed information about the database state for debugging"""
    return jsonify({
        'connection_status': connection_status,
        'items_in_db': real_database.get_items() if connection_status else "Not accessible",
        'next_id': real_database.next_id if connection_status else "Not accessible"
    })

if __name__ == '__main__':
    print("Starting RDS Demo App in local development mode...")
    print("Open http://localhost:8080 in your browser")
    print("\nTesting Features:")
    print("- Use the toggle button in the UI to control the mock RDS connection")
    print("- When 'connected', you can add/view/delete items")
    print("- When 'disconnected', you'll see error messages and the red X in the diagram")
    print("- View database status at: http://localhost:8080/db-status")
    
    app.run(debug=True, host='0.0.0.0', port=8080)