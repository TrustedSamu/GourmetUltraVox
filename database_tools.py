from firebase_service import FirebaseService
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from google.cloud import firestore

class FirebaseEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, firestore.SERVER_TIMESTAMP):
            return None
        return super().default(obj)

class DatabaseTools:
    def __init__(self):
        self.firebase = FirebaseService()

    def explore_database(self) -> Dict[str, List[str]]:
        """Explore the database structure by listing all collections and their documents."""
        try:
            collections = self.firebase.db.collections()
            structure = {}
            
            for collection in collections:
                docs = collection.stream()
                doc_ids = [doc.id for doc in docs]
                structure[collection.id] = doc_ids
            
            return structure
        except Exception as e:
            logging.error(f"Error exploring database: {e}")
            return {}

    # User Operations
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user information by ID."""
        try:
            return self.firebase.get_document("users", user_id)
        except Exception as e:
            logging.error(f"Error getting user: {e}")
            return None

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users from the database."""
        try:
            return self.firebase.get_collection("users")
        except Exception as e:
            logging.error(f"Error getting all users: {e}")
            return []

    # Customer Operations
    def get_customer(self, customer_id: str) -> Optional[Dict[str, Any]]:
        """Get customer information by ID."""
        try:
            return self.firebase.get_document("customers", customer_id)
        except Exception as e:
            logging.error(f"Error getting customer: {e}")
            return None

    def get_all_customers(self) -> List[Dict[str, Any]]:
        """Get all customers from the database."""
        try:
            return self.firebase.get_collection("customers")
        except Exception as e:
            logging.error(f"Error getting all customers: {e}")
            return []

    def update_customer(self, customer_id: str, data: Dict[str, Any]) -> bool:
        """Update customer information."""
        try:
            return self.firebase.update_document("customers", customer_id, data)
        except Exception as e:
            logging.error(f"Error updating customer: {e}")
            return False

    # Conversation Operations
    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get conversation details by ID."""
        try:
            return self.firebase.get_document("conversations", conversation_id)
        except Exception as e:
            logging.error(f"Error getting conversation: {e}")
            return None

    def get_ultravox_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get Ultravox conversation details by ID."""
        try:
            return self.firebase.get_document("ultravox_conversations", conversation_id)
        except Exception as e:
            logging.error(f"Error getting Ultravox conversation: {e}")
            return None

    def get_all_conversations(self) -> List[Dict[str, Any]]:
        """Get all conversations from the database."""
        try:
            return self.firebase.get_collection("conversations")
        except Exception as e:
            logging.error(f"Error getting all conversations: {e}")
            return []

    def get_all_ultravox_conversations(self) -> List[Dict[str, Any]]:
        """Get all Ultravox conversations from the database."""
        try:
            return self.firebase.get_collection("ultravox_conversations")
        except Exception as e:
            logging.error(f"Error getting all Ultravox conversations: {e}")
            return []

    def save_conversation(self, conversation_data: Dict[str, Any], is_ultravox: bool = False) -> Optional[str]:
        """Save a new conversation to the database."""
        try:
            collection = "ultravox_conversations" if is_ultravox else "conversations"
            conversation_data["timestamp"] = datetime.now().isoformat()
            return self.firebase.add_to_collection(collection, conversation_data)
        except Exception as e:
            logging.error(f"Error saving conversation: {e}")
            return None

    # Service Status Operations
    def get_service_status(self, status_id: str = "416") -> Optional[Dict[str, Any]]:
        """Get current service status."""
        try:
            return self.firebase.get_document("service_status", status_id)
        except Exception as e:
            logging.error(f"Error getting service status: {e}")
            return None

    def update_service_status(self, status_id: str, status_data: Dict[str, Any]) -> bool:
        """Update service status information."""
        try:
            return self.firebase.update_document("service_status", status_id, status_data)
        except Exception as e:
            logging.error(f"Error updating service status: {e}")
            return False

    # Tariff Operations
    def get_tariff(self, tariff_id: str) -> Optional[Dict[str, Any]]:
        """Get tariff information by ID."""
        try:
            return self.firebase.get_document("tariffs", tariff_id)
        except Exception as e:
            logging.error(f"Error getting tariff: {e}")
            return None

    def get_all_tariffs(self) -> List[Dict[str, Any]]:
        """Get all available tariffs."""
        try:
            return self.firebase.get_collection("tariffs")
        except Exception as e:
            logging.error(f"Error getting all tariffs: {e}")
            return []

    def get_residential_tariff(self) -> Optional[Dict[str, Any]]:
        """Get the residential standard tariff."""
        try:
            return self.firebase.get_document("tariffs", "residential_standard")
        except Exception as e:
            logging.error(f"Error getting residential tariff: {e}")
            return None

if __name__ == "__main__":
    # Example usage and database structure exploration
    tools = DatabaseTools()
    
    print("Database Structure:")
    structure = tools.explore_database()
    print(json.dumps(structure, indent=2))
    
    # Example: Get all users
    users = tools.get_all_users()
    print("\nUsers:")
    print(json.dumps(users, indent=2, cls=FirebaseEncoder))
    
    # Example: Get service status
    status = tools.get_service_status()
    print("\nService Status:")
    print(json.dumps(status, indent=2, cls=FirebaseEncoder))
    
    # Example: Get all tariffs
    tariffs = tools.get_all_tariffs()
    print("\nTariffs:")
    print(json.dumps(tariffs, indent=2, cls=FirebaseEncoder)) 