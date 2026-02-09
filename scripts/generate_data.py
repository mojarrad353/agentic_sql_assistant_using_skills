import psycopg2
import random
from faker import Faker
import os
import sys

# Add src to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from src.sql_assistant.config import get_settings

fake = Faker()

def create_connection():
    """Create a database connection to the PostgreSQL database."""
    settings = get_settings()
    conn = None
    try:
        conn = psycopg2.connect(
            host=settings.POSTGRES_HOST,
            database=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            port=settings.POSTGRES_PORT
        )
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
    return conn

def create_tables(conn):
    print("Creating tables...")
    cur = conn.cursor()
    
    # Drop tables if they exist (cascade will handle dependencies)
    cur.execute("DROP TABLE IF EXISTS order_items CASCADE;")
    cur.execute("DROP TABLE IF EXISTS orders CASCADE;")
    cur.execute("DROP TABLE IF EXISTS customers CASCADE;")
    cur.execute("DROP TABLE IF EXISTS stock_movements CASCADE;")
    cur.execute("DROP TABLE IF EXISTS inventory CASCADE;")
    cur.execute("DROP TABLE IF EXISTS warehouses CASCADE;")
    cur.execute("DROP TABLE IF EXISTS products CASCADE;")
    
    cur.execute("""
        CREATE TABLE customers (
            customer_id SERIAL PRIMARY KEY,
            name TEXT,
            email TEXT,
            signup_date TEXT,
            status TEXT,
            customer_tier TEXT
        );
    """)
    
    cur.execute("""
        CREATE TABLE orders (
            order_id SERIAL PRIMARY KEY,
            customer_id INTEGER,
            order_date TEXT,
            status TEXT,
            total_amount REAL,
            sales_region TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        );
    """)
    
    cur.execute("""
        CREATE TABLE order_items (
            item_id SERIAL PRIMARY KEY,
            order_id INTEGER,
            product_id INTEGER,
            quantity INTEGER,
            unit_price REAL,
            discount_percent REAL,
            FOREIGN KEY (order_id) REFERENCES orders (order_id)
        );
    """)

    # Inventory Schema
    cur.execute("""
        CREATE TABLE products (
            product_id SERIAL PRIMARY KEY,
            product_name TEXT,
            sku TEXT,
            category TEXT,
            unit_cost REAL,
            reorder_point INTEGER,
            discontinued INTEGER
        );
    """)
    
    cur.execute("""
        CREATE TABLE warehouses (
            warehouse_id SERIAL PRIMARY KEY,
            warehouse_name TEXT,
            location TEXT,
            capacity INTEGER
        );
    """)
    
    cur.execute("""
        CREATE TABLE inventory (
            inventory_id SERIAL PRIMARY KEY,
            product_id INTEGER,
            warehouse_id INTEGER,
            quantity_on_hand INTEGER,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products (product_id),
            FOREIGN KEY (warehouse_id) REFERENCES warehouses (warehouse_id)
        );
    """)
    
    # Optional logic: stock movements
    cur.execute("""
         CREATE TABLE stock_movements (
            movement_id SERIAL PRIMARY KEY,
            product_id INTEGER,
            warehouse_id INTEGER,
            movement_type TEXT,
            quantity INTEGER,
            movement_date TEXT,
            reference_number TEXT,
            FOREIGN KEY (product_id) REFERENCES products (product_id),
            FOREIGN KEY (warehouse_id) REFERENCES warehouses (warehouse_id)
        );
    """)
    
    cur.close()

def generate_data(conn):
    print("Generating data...")
    cur = conn.cursor()
    
    # --- Customers ---
    print("  - Customers")
    customer_ids = []
    tiers = ['bronze', 'silver', 'gold', 'platinum']
    statuses = ['active', 'inactive']
    for _ in range(100):
        name = fake.name()
        email = fake.email()
        signup_date = fake.date_between(start_date='-2y', end_date='today').isoformat()
        status = random.choice(statuses)
        tier = random.choice(tiers)
        
        cur.execute(
            "INSERT INTO customers (name, email, signup_date, status, customer_tier) VALUES (%s, %s, %s, %s, %s) RETURNING customer_id",
            (name, email, signup_date, status, tier)
        )
        customer_ids.append(cur.fetchone()[0])

    # --- Products ---
    print("  - Products")
    product_ids = []
    categories = ['Electronics', 'Clothing', 'Home', 'Toys']
    for _ in range(50):
        pname = fake.word().title() + " " + fake.word().title()
        sku = fake.ean()
        category = random.choice(categories)
        cost = round(random.uniform(5, 500), 2)
        reorder = random.randint(10, 100)
        disc = 1 if random.choice([True, False]) else 0
        
        cur.execute(
            "INSERT INTO products (product_name, sku, category, unit_cost, reorder_point, discontinued) VALUES (%s, %s, %s, %s, %s, %s) RETURNING product_id",
            (pname, sku, category, cost, reorder, disc)
        )
        product_ids.append(cur.fetchone()[0])

    # --- Warehouses ---
    print("  - Warehouses")
    warehouse_ids = []
    for _ in range(5):
        wname = f"Warehouse {fake.city()}"
        loc = fake.address()
        cap = random.randint(1000, 10000)
        
        cur.execute(
            "INSERT INTO warehouses (warehouse_name, location, capacity) VALUES (%s, %s, %s) RETURNING warehouse_id",
            (wname, loc, cap)
        )
        warehouse_ids.append(cur.fetchone()[0])
        
    # --- Inventory ---
    print("  - Inventory")
    for pid in product_ids:
        for wid in warehouse_ids:
            if random.random() > 0.3: # 70% chance product is in warehouse
                qty = random.randint(0, 500)
                cur.execute(
                    "INSERT INTO inventory (product_id, warehouse_id, quantity_on_hand) VALUES (%s, %s, %s)",
                    (pid, wid, qty)
                )

    # --- Orders & Order Items ---
    print("  - Orders & Items")
    order_statuses = ['pending', 'completed', 'cancelled', 'refunded']
    regions = ['north', 'south', 'east', 'west']
    
    for _ in range(300):
        cid = random.choice(customer_ids)
        order_date = fake.date_between(start_date='-1y', end_date='today').isoformat()
        status = random.choice(order_statuses)
        region = random.choice(regions)
        
        cur.execute(
            "INSERT INTO orders (customer_id, order_date, status, total_amount, sales_region) VALUES (%s, %s, %s, %s, %s) RETURNING order_id",
            (cid, order_date, status, 0, region)
        )
        order_id = cur.fetchone()[0]
        
        # Items
        num_items = random.randint(1, 5)
        batch_total = 0
        for _ in range(num_items):
            pid = random.choice(product_ids)
            qty = random.randint(1, 10)
            u_price = round(random.uniform(10, 200), 2)
            disc = round(random.uniform(0, 10), 2)
            
            line_total = qty * u_price * (1 - disc/100)
            batch_total += line_total
            
            cur.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount_percent) VALUES (%s, %s, %s, %s, %s)",
                (order_id, pid, qty, u_price, disc)
            )
        
        # Update total
        cur.execute("UPDATE orders SET total_amount = %s WHERE order_id = %s", (round(batch_total, 2), order_id))

    cur.close()

def main():
    conn = create_connection()
    if conn is not None:
        create_tables(conn)
        generate_data(conn)
        conn.commit()
        conn.close()
        print(f"Success! PostgreSQL database populated.")
    else:
        print("Error! Cannot create the database connection.")

if __name__ == "__main__":
    main()
