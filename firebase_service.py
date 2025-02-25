import firebase_admin
from firebase_admin import credentials, firestore
from typing import Any, Dict, List, Optional
import os
import json

class FirebaseService:
    """Service class to handle Firebase database operations."""
    
    def __init__(self, credentials_path: str = "Firebase-credentials.json"):
        """Initialize Firebase with credentials."""
        if not firebase_admin._apps:
            cred = credentials.Certificate(credentials_path)
            firebase_admin.initialize_app(cred)
        self.db = firestore.client()

    def get_document(self, collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a document from Firestore."""
        try:
            doc_ref = self.db.collection(collection).document(doc_id)
            doc = doc_ref.get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            print(f"Error getting document: {e}")
            return None

    def set_document(self, collection: str, doc_id: str, data: Dict[str, Any]) -> bool:
        """Set a document in Firestore."""
        try:
            doc_ref = self.db.collection(collection).document(doc_id)
            doc_ref.set(data)
            return True
        except Exception as e:
            print(f"Error setting document: {e}")
            return False

    def update_document(self, collection: str, doc_id: str, data: Dict[str, Any]) -> bool:
        """Update a document in Firestore."""
        try:
            # Clean up the document path (remove any trailing slashes)
            doc_id = doc_id.strip().rstrip('/')
            collection = collection.strip().rstrip('/')
            
            # Replace ${SERVER_TIMESTAMP} with actual server timestamp
            def replace_server_timestamp(d):
                if isinstance(d, dict):
                    return {k: firestore.SERVER_TIMESTAMP if v == "${SERVER_TIMESTAMP}" 
                          else replace_server_timestamp(v) for k, v in d.items()}
                elif isinstance(d, list):
                    return [replace_server_timestamp(v) for v in d]
                return d
            
            data = replace_server_timestamp(data)
            
            doc_ref = self.db.collection(collection).document(doc_id)
            doc_ref.update(data)
            return True
        except Exception as e:
            print(f"Error updating document: {e}")
            return False

    def delete_document(self, collection: str, doc_id: str) -> bool:
        """Delete a document from Firestore."""
        try:
            doc_ref = self.db.collection(collection).document(doc_id)
            doc_ref.delete()
            return True
        except Exception as e:
            print(f"Error deleting document: {e}")
            return False

    def get_collection(self, collection: str) -> List[Dict[str, Any]]:
        """Retrieve all documents from a collection."""
        try:
            docs = self.db.collection(collection).stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"Error getting collection: {e}")
            return []

    def add_to_collection(self, collection: str, data: Dict[str, Any]) -> Optional[str]:
        """Add a new document to a collection with auto-generated ID."""
        try:
            doc_ref = self.db.collection(collection).add(data)
            return doc_ref[1].id
        except Exception as e:
            print(f"Error adding document: {e}")
            return None 