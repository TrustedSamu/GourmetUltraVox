from firebase_service import FirebaseService
from database_tools import DatabaseTools
import json
from datetime import datetime, timedelta

def update_customer_abschlag(kundennummer: str, new_abschlag_amount: float) -> dict:
    """
    Updates the Abschlag amount for a specific customer.
    
    Args:
        kundennummer (str): The kundennummer of the customer to update
        new_abschlag_amount (float): The new Abschlag amount to set
        
    Returns:
        dict: A dictionary containing:
            - success (bool): Whether the update was successful
            - message (str): A message describing the result
            - old_amount (float, optional): The previous Abschlag amount if it existed
            - new_amount (float): The new Abschlag amount
            - error (str, optional): Error message if something went wrong
    """
    try:
        if not kundennummer or not isinstance(kundennummer, str) or kundennummer.strip() == "":
            return {
                "success": False,
                "message": "Bitte geben Sie eine gültige Kundennummer an",
                "error": "Invalid customer number"
            }
        
        # Convert amount to float if it's a string or int
        try:
            new_abschlag_amount = float(new_abschlag_amount)
        except (ValueError, TypeError):
            return {
                "success": False,
                "message": "Der Abschlagsbetrag muss eine gültige Zahl sein",
                "error": "Invalid amount format"
            }
            
        if new_abschlag_amount <= 0:
            return {
                "success": False,
                "message": "Der Abschlagsbetrag muss größer als 0 sein",
                "error": "Amount must be positive"
            }
            
        db_tools = DatabaseTools()
        
        # Get the customer directly using kundennummer as document ID
        customer = db_tools.get_customer(kundennummer.strip())
        
        if not customer:
            return {
                "success": False,
                "message": f"Kunde mit Kundennummer {kundennummer} wurde nicht gefunden",
                "error": "Customer not found"
            }
            
        # Store the old amount if it exists
        old_amount = customer.get('abschlag', {}).get('betrag', None)
        
        # Calculate next due date (if not present, set to one month from now)
        next_due_date = customer.get('abschlag', {}).get('naechsteFaelligkeit', 
                                                    (datetime.now() + timedelta(days=30)).isoformat())
        
        # Prepare the update data with the nested structure
        update_data = {
            "abschlag": {
                "betrag": new_abschlag_amount,  # Already ensured to be float
                "naechsteFaelligkeit": next_due_date,
                "zahlungsrhythmus": customer.get('abschlag', {}).get('zahlungsrhythmus', 'monatlich'),
                "letzte_aenderung": "${SERVER_TIMESTAMP}"
            }
        }
        
        # Update the customer record using kundennummer as document ID
        success = db_tools.update_customer(kundennummer.strip(), update_data)
        
        if success:
            return {
                "success": True,
                "message": f"Abschlag erfolgreich auf {new_abschlag_amount} Euro aktualisiert",
                "old_amount": old_amount,
                "new_amount": new_abschlag_amount
            }
        else:
            return {
                "success": False,
                "message": "Fehler beim Aktualisieren des Abschlags",
                "error": "Update failed"
            }
            
    except Exception as e:
        return {
            "success": False,
            "message": "Ein Fehler ist aufgetreten",
            "error": str(e)
        }

# Example usage
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 3:
        print("Usage: python abschlag_update.py <kundennummer> <new_abschlag_amount>")
        sys.exit(1)
        
    kundennummer = sys.argv[1]
    try:
        new_amount = float(sys.argv[2])
    except ValueError:
        print("Error: Abschlag amount must be a number")
        sys.exit(1)
        
    result = update_customer_abschlag(kundennummer, new_amount)
    print(json.dumps(result, indent=2, ensure_ascii=False)) 