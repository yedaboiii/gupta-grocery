import os
from database import init_db
from flask import Flask
from routes.admin import admin_bp
from routes.customer import customer_bp
from routes.pos import pos_bp

app = Flask(__name__)
app.secret_key = "swaraj_grocery_secure_admin_secret_key"

# Initialize SQLite database on startup
init_db()

# Register Blueprints
app.register_blueprint(customer_bp)
app.register_blueprint(pos_bp)
app.register_blueprint(admin_bp)

if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5001))
  app.run(host="0.0.0.0", port=port)