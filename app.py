# app.py - NEW AND IMPROVED VERSION
import os
import psycopg2
from flask import Flask, request, jsonify, Response
from functools import wraps

app = Flask(__name__)

# --- DATABASE SETUP ---
# It reads the URL and credentials from the Environment Variables you set in Render
DATABASE_URL = os.environ.get('DATABASE_URL')
ISSUER_ID = os.environ.get('ISSUER_ID')
ISSUER_SECRET = os.environ.get('ISSUER_SECRET')

def get_db_connection():
    """Creates a database connection."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def setup_database():
    """Creates the 'licenses' table if it doesn't already exist."""
    # This print statement will show up in your Render logs!
    print("Checking and setting up database table...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # This is a sample schema. You can add more columns if you need them.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS licenses (
                id SERIAL PRIMARY KEY,
                license_key VARCHAR(255) UNIQUE NOT NULL,
                product_id VARCHAR(255) NOT NULL,
                status VARCHAR(50) DEFAULT 'available',
                entity_id VARCHAR(255),
                date_created TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                date_assigned TIMESTAMP WITH TIME ZONE
            );
        ''')
        conn.commit()
        cur.close()
        conn.close()
        print("Database table setup complete.")
    except Exception as e:
        print(f"FATAL: Error setting up database: {e}")

# --- AUTHENTICATION ---
def check_auth(username, password):
    """This function is called to check if a username/password combination is valid."""
    return username == ISSUER_ID and password == ISSUER_SECRET

def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- ROUTES ---
@app.route("/")
def index():
    # A simple route to check if your server is alive.
    return "Cloud Zoo Issuer Callback Server is running."

@app.route("/get_license")
@requires_auth
@app.route("/add_license", methods=["POST"])
@requires_auth
def add_license():
    """
    Checks if a license is available and assigns it to an entity if it is.
    This is called when a user adds a license to their account.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"description": "Request body is missing"}), 400

        # Safely get the data from the incoming JSON
        license_info = data.get('license', {})
        license_key = license_info.get('key')
        product_id = license_info.get('aud')
        entity_id = data.get('entityId')

        if not all([license_key, product_id, entity_id]):
            return jsonify({"description": "Request is missing license key, product ID, or entity ID."}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        # First, find the license and check its current status
        cur.execute(
            "SELECT status FROM licenses WHERE license_key = %s AND product_id = %s;",
            (license_key, product_id)
        )
        result = cur.fetchone()

        # CASE 1: The license key doesn't exist at all.
        if not result:
            cur.close()
            conn.close()
            print(f"CONFLICT: Add attempt for non-existent key: {license_key}")
            return jsonify({"description": "The provided license key does not exist."}), 409 # 409 Conflict

        current_status = result[0]

        # CASE 2: The license is already assigned.
        if current_status == 'assigned':
            cur.close()
            conn.close()
            print(f"CONFLICT: Add attempt for already assigned key: {license_key}")
            return jsonify({"description": "This license key has already been assigned to another account."}), 409
        
        # CASE 3 (Success): The license is available.
        if current_status == 'available':
            # Assign the license by updating the database record
            cur.execute(
                """
                UPDATE licenses 
                SET status = 'assigned', entity_id = %s, date_assigned = NOW() 
                WHERE license_key = %s;
                """,
                (entity_id, license_key)
            )
            conn.commit()
            cur.close()
            conn.close()

            print(f"SUCCESS: Assigned license {license_key} to entity {entity_id}")

            # Per the documentation, we must return a License Cluster object on success.
            # This confirms to Cloud Zoo what was just added.
            license_cluster_response = {
                "licenses": [
                    {
                        "id": license_key,
                        "key": license_key,
                        "aud": product_id,
                        "iss": ISSUER_ID,
                        "status": "assigned",
                        "titles": {"en": "Curve Cutter"} # This should match your product name
                    }
                ]
            }
            return jsonify(license_cluster_response), 200

        # Fallback case for any other status (e.g., 'revoked')
        cur.close()
        conn.close()
        return jsonify({"description": "This license cannot be added at this time."}), 409

    except Exception as e:
        print(f"FATAL ERROR in /add_license: {e}")
        return jsonify({"description": "An internal server error occurred."}), 500
def get_license():
    license_key = request.args.get("key")
    product_id_req = request.args.get("aud") # 'aud' is the product ID

    if not license_key or not product_id_req:
        return jsonify({"description": "Missing license key or product ID."}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    # Check if a license exists and is marked as available
    cur.execute(
        "SELECT license_key, product_id, status FROM licenses WHERE license_key = %s AND product_id = %s;",
        (license_key, product_id_req)
    )
    license_data = cur.fetchone()
    cur.close()
    conn.close()

    if license_data:
        key, prod_id, status = license_data
        # This is where you would format the response into a proper "License object"
        # as specified by the McNeel documentation. This is a basic example.
        print(f"SUCCESS: Valid license found for {key}")
        return jsonify({
            "id": key,
            "key": key,
            "aud": prod_id,
            "title": "My Awesome Plug-in" # You can customize this
        }), 200
    else:
        print(f"INFO: No valid license found for key {license_key}")
        return jsonify({"description": "License key not found for the specified product."}), 404
        
# TODO: You will need to implement the /add_license and /remove_license routes
# These routes would use UPDATE SQL commands to change the license status and assign it
# to an entity_id, or set it back to 'available'.

# Run the setup function once on application startup.
setup_database()

if __name__ == "__main__":
    # This part is needed for the server to run correctly on Render
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
