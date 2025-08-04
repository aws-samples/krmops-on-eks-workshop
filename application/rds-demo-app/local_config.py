import os

# Set environment variables for local testing
os.environ['DB_HOST'] = 'localhost'
os.environ['DB_NAME'] = 'testdb'
os.environ['DB_USER'] = 'testuser'
os.environ['DB_PASSWORD'] = 'testpassword'

# Create a persistent storage for the "real" database
# This simulates the actual RDS database that persists regardless of connection status
class RealDatabase:
    def __init__(self):
        self.data = []
        self.next_id = 1
    
    def add_item(self, name, content):
        item_id = self.next_id
        self.data.append({
            'id': item_id,
            'name': name,
            'content': content
        })
        self.next_id += 1
        return item_id
    
    def get_items(self):
        return self.data.copy()
    
    def delete_item(self, item_id):
        initial_len = len(self.data)
        self.data = [item for item in self.data if item['id'] != item_id]
        return initial_len - len(self.data) > 0  # Returns True if item was deleted

# Create an in-memory SQLite-like implementation using dictionaries
class MockConnection:
    def __init__(self, real_db):
        self.real_db = real_db
        
    def cursor(self):
        return MockCursor(self)
        
    def commit(self):
        pass
        
    def close(self):
        pass

class MockCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 0
        self.last_query = None
        self.last_params = None
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
        
    def execute(self, query, params=None):
        self.last_query = query
        self.last_params = params
        
        if "CREATE TABLE" in query:
            return
        elif "SELECT * FROM items" in query:
            return
        elif "INSERT INTO items" in query:
            name, content = params
            # Add to the real database
            self.connection.real_db.add_item(name, content)
            self.rowcount = 1
            return
        elif "DELETE FROM items WHERE id" in query:
            item_id = params[0]
            # Delete from the real database
            success = self.connection.real_db.delete_item(item_id)
            self.rowcount = 1 if success else 0
            return
        elif "SELECT 1" in query:
            return
            
    def fetchall(self):
        if self.last_query and "SELECT * FROM items" in self.last_query:
            # Return data from the real database
            return self.connection.real_db.get_items()
        return []
        
    def fetchone(self):
        return {'1': 1}

# Create the real database that persists data
real_database = RealDatabase()

# Create the mock connection that interacts with the real database
mock_connection = MockConnection(real_database)