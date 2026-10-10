"""
Empirical DDL, Constraint, Concurrency, and Pipeline Stress-Test Suite
MedConnect Clinic Appointment Management System
Evaluates: schema.sql (PostgreSQL 16) via AST parsing and SQLite3 empirical execution harness.
"""

import re
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import sqlglot

SCHEMA_SQL_PATH = "/home/rajeev/Desktop/projects/dbms-clinic-project/schema.sql"

def get_sqlite_connection():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Register regex function for email and string validation
    def regexp(expr, item):
        if item is None:
            return None
        return bool(re.search(expr, item, re.IGNORECASE))

    conn.create_function("regexp", 2, regexp)
    return conn

def convert_pg_to_sqlite(pg_sql: str) -> str:
    """
    Transforms PostgreSQL DDL to SQLite-compatible DDL while preserving
    every single table, column, constraint, check, unique index, and foreign key.
    """
    sql = pg_sql
    # Replace serial with integer primary key autoincrement
    sql = re.sub(r'\bSERIAL\s+PRIMARY\s+KEY\b', 'INTEGER PRIMARY KEY AUTOINCREMENT', sql, flags=re.IGNORECASE)
    # Replace timestamptz with timestamp
    sql = re.sub(r'\bTIMESTAMPTZ\b', 'TIMESTAMP', sql, flags=re.IGNORECASE)
    # Replace regex operator ~* with REGEXP
    sql = re.sub(r'(\w+)\s+~\*\s+(\'[^\']+\')', r'\1 REGEXP \2', sql)
    # Replace char_length with length
    sql = re.sub(r'\bchar_length\(', 'length(', sql, flags=re.IGNORECASE)
    # Strip CASCADE from DROP TABLE / DROP VIEW for SQLite compatibility
    sql = re.sub(r'(DROP\s+(?:TABLE|VIEW)\s+[^;]+?)\s+CASCADE\s*;', r'\1;', sql, flags=re.IGNORECASE)
    return sql

def setup_test_database(conn):
    """Parses and executes DDL from schema.sql in SQLite memory database."""
    with open(SCHEMA_SQL_PATH, "r") as f:
        raw_sql = f.read()

    cursor = conn.cursor()

    # Extract all CREATE TABLE statements
    tables = re.findall(r'CREATE\s+TABLE\s+.*?;', raw_sql, re.DOTALL | re.IGNORECASE)
    for t in tables:
        cursor.execute(convert_pg_to_sqlite(t))

    # Extract all CREATE INDEX statements (including partial unique indexes)
    indexes = re.findall(r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+.*?;', raw_sql, re.DOTALL | re.IGNORECASE)
    for idx in indexes:
        cursor.execute(convert_pg_to_sqlite(idx))

    # Extract all CREATE VIEW statements
    views = re.findall(r'CREATE\s+OR\s+REPLACE\s+VIEW\s+(.*?);', raw_sql, re.DOTALL | re.IGNORECASE)
    for v in views:
        cursor.execute(convert_pg_to_sqlite('CREATE VIEW ' + v + ';'))

    # Attach SQLite-equivalent inventory check & deduction trigger
    sqlite_trigger = """
    CREATE TRIGGER trg_deduct_medicine_stock
    BEFORE INSERT ON Prescription_Items
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'Insufficient inventory for requested medicine')
        WHERE (SELECT stock_quantity FROM Medicines WHERE medicine_id = NEW.medicine_id) < NEW.quantity;

        UPDATE Medicines
        SET stock_quantity = stock_quantity - NEW.quantity
        WHERE medicine_id = NEW.medicine_id;
    END;
    """
    cursor.execute(sqlite_trigger)
    conn.commit()


class TestSchemaASTVerification(unittest.TestCase):
    """Verifies that schema.sql adheres strictly to standard PostgreSQL 16 DDL syntax via AST parser."""

    def test_sqlglot_postgres_ast_parse(self):
        with open(SCHEMA_SQL_PATH, "r") as f:
            sql_content = f.read()

        parsed_statements = sqlglot.parse(sql_content, read="postgres")
        self.assertGreater(len(parsed_statements), 40, "Schema must parse into at least 40 expressions")

        # Verify all 10 core tables are defined in CREATE TABLE statements
        table_names = set()
        for expr in parsed_statements:
            if expr.key == "create":
                # Check if it creates a table
                this_item = expr.this
                if this_item and hasattr(this_item, "this") and hasattr(this_item.this, "this"):
                    t_name = str(this_item.this.this).strip('"')
                    table_names.add(t_name.lower())

        expected_tables = {
            "departments", "doctors", "doctor_schedules", "patients",
            "appointments", "medical_records", "prescriptions",
            "medicines", "prescription_items", "invoices"
        }
        for expected in expected_tables:
            self.assertIn(expected, table_names, f"Expected table {expected} missing from AST parse")

    def test_sp_generate_invoice_status_precondition(self):
        with open(SCHEMA_SQL_PATH, "r") as f:
            sql_content = f.read()

        self.assertIn("v_appt_status", sql_content, "v_appt_status variable must be declared in sp_generate_invoice")
        self.assertIn("v_appt_status != 'Completed'", sql_content, "sp_generate_invoice must verify status is Completed")
        self.assertIn("Cannot generate invoice for appointment", sql_content, "sp_generate_invoice must raise exception on non-completed status")

    def test_v_daily_department_revenue_filters_paid(self):
        with open(SCHEMA_SQL_PATH, "r") as f:
            sql_content = f.read()

        view_match = re.search(r'CREATE\s+OR\s+REPLACE\s+VIEW\s+v_daily_department_revenue\s+AS\s+(.*?);', sql_content, re.DOTALL | re.IGNORECASE)
        self.assertIsNotNone(view_match, "v_daily_department_revenue view must be defined in schema.sql")
        view_sql = view_match.group(1)
        self.assertIn("payment_status = 'Paid'", view_sql, "v_daily_department_revenue must filter on payment_status = 'Paid'")

    def test_analytical_query_3_ast_parse(self):
        design_path = "/home/rajeev/Desktop/projects/dbms-clinic-project/docs/database_design.md"
        with open(design_path, "r") as f:
            doc_content = f.read()

        # Extract Query 3 SQL block
        q3_match = re.search(r'### Query 3:.*?\n```sql\n(.*?)\n```', doc_content, re.DOTALL)
        self.assertIsNotNone(q3_match, "Query 3 SQL block must be present in docs/database_design.md")
        q3_sql = q3_match.group(1)

        # Parse with sqlglot PostgreSQL dialect
        parsed = sqlglot.parse_one(q3_sql, read="postgres")
        self.assertIsNotNone(parsed, "Query 3 must parse into a valid AST")
        self.assertEqual(parsed.key, "select", "Query 3 root expression must be a SELECT")
        self.assertIn("recentdispenses", [cte.alias.lower() for cte in parsed.ctes], "Query 3 must define RecentDispenses CTE")


class TestDDLAndIntegrityConstraints(unittest.TestCase):
    """Empirically tests DDL table creation, Primary Keys, Foreign Keys, and Check Constraints."""

    def setUp(self):
        self.conn = get_sqlite_connection()
        setup_test_database(self.conn)
        self.cursor = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def _seed_base_entities(self):
        self.cursor.execute("""
            INSERT INTO Departments (dept_name, description, location, contact_number)
            VALUES ('Cardiology', 'Heart health and surgery', 'Building A, Fl 3', '555-0101');
        """)
        self.cursor.execute("""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee, status)
            VALUES (1, 'Alice', 'Smith', 'alice.smith@clinic.org', '555-1001', 'Cardiologist', 'MD, FACC', 150.00, 'Active');
        """)
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, blood_group, phone, email, address)
            VALUES ('John', 'Doe', '1985-06-15', 'Male', 'O+', '555-2001', 'john.doe@example.com', '123 Elm St');
        """)
        self.conn.commit()

    def test_pk_uniqueness_enforcement(self):
        self._seed_base_entities()
        # Attempt duplicate PK on Departments
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Departments (dept_id, dept_name, location)
                VALUES (1, 'Duplicate Dept', 'Wing B');
            """)

    def test_fk_referential_action_restrict_and_cascade(self):
        self._seed_base_entities()
        # 1. Invalid FK on insert Doctor (dept_id 999 does not exist)
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
                VALUES (999, 'Bob', 'Jones', 'bob.jones@clinic.org', '555-1002', 'Neurologist', 'MD', 200.00);
            """)

        # 2. Deleting Department with linked Doctor must be restricted
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("DELETE FROM Departments WHERE dept_id = 1;")

    def test_check_constraints_doctors(self):
        self.cursor.execute("""
            INSERT INTO Departments (dept_name, location) VALUES ('Pediatrics', 'Building B');
        """)
        dept_id = self.cursor.lastrowid

        # Negative consultation fee rejected
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute(f"""
                INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
                VALUES ({dept_id}, 'Bad', 'Fee', 'bad.fee@clinic.org', '555-1111', 'Pediatrician', 'MD', -50.00);
            """)

        # Invalid doctor status rejected
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute(f"""
                INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee, status)
                VALUES ({dept_id}, 'Bad', 'Status', 'bad.status@clinic.org', '555-1112', 'Pediatrician', 'MD', 100.00, 'Terminated');
            """)

        # Invalid doctor email format rejected
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute(f"""
                INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
                VALUES ({dept_id}, 'Bad', 'Email', 'not-an-email', '555-1113', 'Pediatrician', 'MD', 100.00);
            """)

    def test_check_constraints_doctor_schedules(self):
        self._seed_base_entities()
        # Invalid day of week rejected
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Doctor_Schedules (doctor_id, day_of_week, start_time, end_time, slot_duration_minutes)
                VALUES (1, 'Funday', '09:00:00', '12:00:00', 30);
            """)

        # Start time >= End time rejected
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Doctor_Schedules (doctor_id, day_of_week, start_time, end_time, slot_duration_minutes)
                VALUES (1, 'Monday', '14:00:00', '13:00:00', 30);
            """)

        # Start time == End time rejected
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Doctor_Schedules (doctor_id, day_of_week, start_time, end_time, slot_duration_minutes)
                VALUES (1, 'Monday', '09:00:00', '09:00:00', 30);
            """)

        # Invalid slot duration rejected (e.g. 25 mins)
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Doctor_Schedules (doctor_id, day_of_week, start_time, end_time, slot_duration_minutes)
                VALUES (1, 'Monday', '09:00:00', '12:00:00', 25);
            """)

    def test_check_constraints_patients(self):
        # Invalid gender rejected
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
                VALUES ('Jane', 'Doe', '1990-01-01', 'Alien', '555-2002', '456 Oak St');
            """)

        # Invalid blood group rejected
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Patients (first_name, last_name, date_of_birth, gender, blood_group, phone, address)
                VALUES ('Jane', 'Doe', '1990-01-01', 'Female', 'Z+', '555-2003', '456 Oak St');
            """)

        # Invalid DOB prior to 1900-01-01 rejected by chk_patient_dob_valid
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
                VALUES ('Ancient', 'Person', '1899-12-31', 'Male', '555-2004', '789 Old St');
            """)

    def test_medical_records_nullable_appointment_and_set_null(self):
        self._seed_base_entities()
        # 1. Medical record with appointment_id = NULL succeeds (legacy/walk-in record)
        self.cursor.execute("""
            INSERT INTO Medical_Records (appointment_id, patient_id, doctor_id, symptoms, diagnosis, clinical_notes)
            VALUES (NULL, 1, 1, 'Legacy migraine symptoms', 'Migraine with aura', 'Historical paper chart ingested.');
        """)
        rec1_id = self.cursor.lastrowid
        self.assertIsNotNone(rec1_id)
        self.conn.commit()

        # 2. Medical record with valid appointment
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-10-22', '11:00:00', 'Completed');
        """)
        appt_id = self.cursor.lastrowid

        self.cursor.execute(f"""
            INSERT INTO Medical_Records (appointment_id, patient_id, doctor_id, symptoms, diagnosis, clinical_notes)
            VALUES ({appt_id}, 1, 1, 'Follow-up checkup', 'Hypertension controlled', 'Routine checkup.');
        """)
        rec2_id = self.cursor.lastrowid
        self.conn.commit()

        # 3. Deleting appointment sets appointment_id to NULL (ON DELETE SET NULL)
        self.cursor.execute(f"DELETE FROM Appointments WHERE appointment_id = {appt_id};")
        self.conn.commit()

        self.cursor.execute(f"SELECT appointment_id FROM Medical_Records WHERE record_id = {rec2_id};")
        result = self.cursor.fetchone()
        self.assertIsNone(result[0], "appointment_id must be SET NULL upon appointment deletion")

    def test_check_constraints_invoices(self):
        self._seed_base_entities()
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-10-15', '10:00:00', 'Completed');
        """)
        appt_id = self.cursor.lastrowid

        # Total amount must equal consultation_fee + medicine_total
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute(f"""
                INSERT INTO Invoices (appointment_id, patient_id, consultation_fee, medicine_total, total_amount, payment_status)
                VALUES ({appt_id}, 1, 150.00, 50.00, 199.99, 'Pending');
            """)

        # Status 'Paid' requires paid_at and payment_method to be NOT NULL
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute(f"""
                INSERT INTO Invoices (appointment_id, patient_id, consultation_fee, medicine_total, total_amount, payment_status, paid_at, payment_method)
                VALUES ({appt_id}, 1, 150.00, 50.00, 200.00, 'Paid', NULL, 'Card');
            """)


class TestConcurrencyAndPartialUniqueIndex(unittest.TestCase):
    """
    Stress-tests the partial unique index:
    CREATE UNIQUE INDEX uq_doctor_slot_active
    ON Appointments (doctor_id, appointment_date, appointment_time)
    WHERE status NOT IN ('Cancelled');
    """

    def setUp(self):
        self.conn = get_sqlite_connection()
        setup_test_database(self.conn)
        self.cursor = self.conn.cursor()

        # Seed doctor and 2 patients
        self.cursor.execute("INSERT INTO Departments (dept_name, location) VALUES ('General Medicine', 'Fl 1');")
        self.cursor.execute("""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES (1, 'Sarah', 'Connor', 'sarah.connor@clinic.org', '555-3001', 'General Physician', 'MBBS, MD', 100.00);
        """)
        self.cursor.execute("""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES (1, 'John', 'Watson', 'john.watson@clinic.org', '555-3002', 'General Physician', 'MBBS', 100.00);
        """)
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
            VALUES ('Patient', 'One', '1990-01-01', 'Male', '555-4001', '100 Main St');
        """)
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
            VALUES ('Patient', 'Two', '1992-02-02', 'Female', '555-4002', '200 Main St');
        """)
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
            VALUES ('Patient', 'Three', '1994-03-03', 'Other', '555-4003', '300 Main St');
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_double_booking_collision_blocked(self):
        # Patient 1 books Dr. Sarah Connor for 2026-10-20 at 10:00:00
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-10-20', '10:00:00', 'Scheduled');
        """)
        self.conn.commit()

        # Patient 2 attempts to book the EXACT SAME doctor, date, and time
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.cursor.execute("""
                INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                VALUES (2, 1, '2026-10-20', '10:00:00', 'Scheduled');
            """)
        self.assertIn("UNIQUE constraint failed", str(ctx.exception))

    def test_orthogonal_slots_succeed(self):
        # 1. Patient 1 books Dr. 1 at 10:00:00
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-10-20', '10:00:00', 'Scheduled');
        """)
        # 2. Patient 2 books Dr. 1 at 10:30:00 (different time) -> SUCCEEDS
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (2, 1, '2026-10-20', '10:30:00', 'Scheduled');
        """)
        # 3. Patient 3 books Dr. 2 at 10:00:00 (different doctor, same time) -> SUCCEEDS
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (3, 2, '2026-10-20', '10:00:00', 'Scheduled');
        """)
        self.conn.commit()

    def test_slot_reuse_on_cancellation_and_multiple_cancelled_coexistence(self):
        # Step 1: Patient 1 reserves slot
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-10-25', '14:00:00', 'Scheduled');
        """)
        appt1_id = self.cursor.lastrowid
        self.conn.commit()

        # Collision check: Patient 2 fails
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                VALUES (2, 1, '2026-10-25', '14:00:00', 'Scheduled');
            """)

        # Step 2: Patient 1 cancels the appointment
        self.cursor.execute(f"UPDATE Appointments SET status = 'Cancelled' WHERE appointment_id = {appt1_id};")
        self.conn.commit()

        # Step 3: Patient 2 now successfully books the newly freed slot
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (2, 1, '2026-10-25', '14:00:00', 'Scheduled');
        """)
        appt2_id = self.cursor.lastrowid
        self.conn.commit()
        self.assertIsNotNone(appt2_id)

        # Step 4: Collision check: Patient 3 fails while Patient 2 is active
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute("""
                INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                VALUES (3, 1, '2026-10-25', '14:00:00', 'Scheduled');
            """)

        # Step 5: Patient 2 also cancels the appointment
        self.cursor.execute(f"UPDATE Appointments SET status = 'Cancelled' WHERE appointment_id = {appt2_id};")
        self.conn.commit()

        # Step 6: Verify two cancelled tuples can coexist for the same slot
        self.cursor.execute("""
            SELECT COUNT(*) FROM Appointments 
            WHERE doctor_id = 1 AND appointment_date = '2026-10-25' AND appointment_time = '14:00:00';
        """)
        count = self.cursor.fetchone()[0]
        self.assertEqual(count, 2, "Both cancelled appointments must coexist in table")

        # Step 7: Patient 3 books the slot now -> SUCCEEDS
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (3, 1, '2026-10-25', '14:00:00', 'Scheduled');
        """)
        appt3_id = self.cursor.lastrowid
        self.conn.commit()
        self.assertIsNotNone(appt3_id)

        # Total tuples for that slot = 3 (2 Cancelled, 1 Scheduled)
        self.cursor.execute("""
            SELECT COUNT(*) FROM Appointments 
            WHERE doctor_id = 1 AND appointment_date = '2026-10-25' AND appointment_time = '14:00:00';
        """)
        total_count = self.cursor.fetchone()[0]
        self.assertEqual(total_count, 3)

    def test_multi_threaded_concurrent_race_condition(self):
        """
        Simulates 10 threads concurrently racing to book the EXACT SAME doctor slot.
        Empirically verifies that exactly 1 transaction succeeds and 9 fail with unique violations.
        """
        successes = []
        failures = []

        def attempt_booking(patient_id):
            conn = get_sqlite_connection()
            setup_test_database(conn)
            # Share data via attaching or using a file-based shared db
            # In SQLite, test with disk database or WAL mode
            conn.close()

        # Create a temporary disk database with WAL for concurrent testing
        import tempfile, os
        temp_db = tempfile.mktemp(suffix=".db")
        conn_init = sqlite3.connect(temp_db)
        conn_init.execute("PRAGMA journal_mode = WAL;")
        conn_init.execute("PRAGMA foreign_keys = ON;")
        def regexp(expr, item):
            if item is None:
                return None
            return bool(re.search(expr, item, re.IGNORECASE))
        conn_init.create_function("regexp", 2, regexp)
        setup_test_database(conn_init)
        
        # Seed base data
        c = conn_init.cursor()
        c.execute("INSERT INTO Departments (dept_name, location) VALUES ('Dermatology', 'Fl 2');")
        c.execute("""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES (1, 'Gregory', 'House', 'gregory.house@clinic.org', '555-9999', 'Diagnostician', 'MD', 250.00);
        """)
        for p in range(1, 11):
            c.execute(f"""
                INSERT INTO Patients (patient_id, first_name, last_name, date_of_birth, gender, phone, address)
                VALUES ({p}, 'Patient', '{p}', '1990-01-01', 'Other', '555-88{p:02d}', 'Test St');
            """)
        conn_init.commit()
        conn_init.close()

        def concurrent_booker(patient_id):
            c_worker = sqlite3.connect(temp_db, timeout=10.0)
            c_worker.execute("PRAGMA foreign_keys = ON;")
            try:
                cur = c_worker.cursor()
                cur.execute(f"""
                    INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                    VALUES ({patient_id}, 1, '2026-11-01', '09:00:00', 'Scheduled');
                """)
                c_worker.commit()
                successes.append(patient_id)
            except sqlite3.IntegrityError:
                failures.append(patient_id)
            finally:
                c_worker.close()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(concurrent_booker, p) for p in range(1, 11)]
            for f in futures:
                f.result()

        try:
            self.assertEqual(len(successes), 1, f"Expected exactly 1 booking success, got {len(successes)}")
            self.assertEqual(len(failures), 9, f"Expected exactly 9 booking failures, got {len(failures)}")
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)
            if os.path.exists(temp_db + "-wal"):
                os.remove(temp_db + "-wal")
            if os.path.exists(temp_db + "-shm"):
                os.remove(temp_db + "-shm")


class TestFullPipelineRelationalIntegrity(unittest.TestCase):
    """
    Empirically verifies the end-to-end data pipeline:
    Department -> Doctor -> Schedule -> Patient -> Appointment -> Medical_Record -> Prescription -> Medicines -> Prescription_Items -> Invoice
    """

    def setUp(self):
        self.conn = get_sqlite_connection()
        setup_test_database(self.conn)
        self.cursor = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def test_full_clinical_encounter_lifecycle(self):
        # 1. Department
        self.cursor.execute("""
            INSERT INTO Departments (dept_name, description, location, contact_number)
            VALUES ('Cardiology', 'Cardiovascular Clinic', 'Building C', '555-5001');
        """)
        dept_id = self.cursor.lastrowid

        # 2. Doctor
        self.cursor.execute(f"""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES ({dept_id}, 'Marcus', 'Welby', 'marcus.welby@clinic.org', '555-5002', 'Cardiology', 'MD', 200.00);
        """)
        doctor_id = self.cursor.lastrowid

        # 3. Doctor Schedule
        self.cursor.execute(f"""
            INSERT INTO Doctor_Schedules (doctor_id, day_of_week, start_time, end_time, slot_duration_minutes)
            VALUES ({doctor_id}, 'Wednesday', '09:00:00', '13:00:00', 30);
        """)

        # 4. Patient
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, blood_group, phone, email, address)
            VALUES ('Robert', 'Smith', '1975-04-12', 'Male', 'A+', '555-6001', 'robert.smith@example.com', '789 Pine Ave');
        """)
        patient_id = self.cursor.lastrowid

        # 5. Appointment
        self.cursor.execute(f"""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status, reason_for_visit)
            VALUES ({patient_id}, {doctor_id}, '2026-10-21', '09:30:00', 'Completed', 'Chest discomfort evaluation');
        """)
        appt_id = self.cursor.lastrowid

        # 6. Medical Record
        self.cursor.execute(f"""
            INSERT INTO Medical_Records (appointment_id, patient_id, doctor_id, symptoms, diagnosis, clinical_notes)
            VALUES ({appt_id}, {patient_id}, {doctor_id}, 'Exertional angina, mild hypertension', 'Stage 1 Essential Hypertension', 'ECG normal sinus rhythm. Commencing ACE inhibitor.');
        """)

        # 7. Prescriptions Header
        self.cursor.execute(f"""
            INSERT INTO Prescriptions (appointment_id, patient_id, doctor_id, instructions)
            VALUES ({appt_id}, {patient_id}, {doctor_id}, 'Take once daily before breakfast with full glass of water.');
        """)
        prescription_id = self.cursor.lastrowid

        # 8. Medicines Catalog
        self.cursor.execute("""
            INSERT INTO Medicines (name, category, manufacturer, unit_price, stock_quantity, reorder_level, expiry_date)
            VALUES ('Lisinopril 10mg', 'Cardiovascular', 'PharmaCare', 15.50, 100, 25, '2028-12-31');
        """)
        med1_id = self.cursor.lastrowid

        self.cursor.execute("""
            INSERT INTO Medicines (name, category, manufacturer, unit_price, stock_quantity, reorder_level, expiry_date)
            VALUES ('Aspirin 81mg', 'Analgesic', 'Bayer Health', 5.00, 200, 30, '2027-06-30');
        """)
        med2_id = self.cursor.lastrowid

        # 9. Prescription Items & Trigger Inventory Deduction Check
        # Prescribe 30 tabs of Lisinopril
        self.cursor.execute(f"""
            INSERT INTO Prescription_Items (prescription_id, medicine_id, dosage, duration_days, quantity)
            VALUES ({prescription_id}, {med1_id}, '10mg daily', 30, 30);
        """)
        # Prescribe 60 tabs of Aspirin
        self.cursor.execute(f"""
            INSERT INTO Prescription_Items (prescription_id, medicine_id, dosage, duration_days, quantity)
            VALUES ({prescription_id}, {med2_id}, '81mg daily', 60, 60);
        """)

        # Verify stock decremented automatically via trigger
        self.cursor.execute(f"SELECT stock_quantity FROM Medicines WHERE medicine_id = {med1_id};")
        self.assertEqual(self.cursor.fetchone()[0], 70, "Lisinopril stock must decrement from 100 to 70")

        self.cursor.execute(f"SELECT stock_quantity FROM Medicines WHERE medicine_id = {med2_id};")
        self.assertEqual(self.cursor.fetchone()[0], 140, "Aspirin stock must decrement from 200 to 140")

        # Commit valid encounter entities up to prescription items
        self.conn.commit()

        # Insufficient inventory failure check:
        # Attempt to order 80 Lisinopril when only 70 remain
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.cursor.execute(f"""
                INSERT INTO Prescription_Items (prescription_id, medicine_id, dosage, duration_days, quantity)
                VALUES ({prescription_id}, {med1_id}, '10mg extra', 80, 80);
            """)
        # Cleanly rollback only the failed insertion attempt
        self.conn.rollback()

        # 10. Financial Invoice Generation & Reconciliation
        consultation_fee = Decimal("200.00")
        # Lisinopril: 30 * 15.50 = 465.00
        # Aspirin: 60 * 5.00 = 300.00
        # Total medicine: 765.00
        medicine_total = Decimal("765.00")
        total_amount = consultation_fee + medicine_total  # 965.00

        self.cursor.execute(f"""
            INSERT INTO Invoices (
                appointment_id, patient_id, consultation_fee, medicine_total,
                total_amount, payment_status, payment_method, paid_at
            ) VALUES (
                {appt_id}, {patient_id}, {float(consultation_fee)}, {float(medicine_total)},
                {float(total_amount)}, 'Paid', 'Card', CURRENT_TIMESTAMP
            );
        """)
        invoice_id = self.cursor.lastrowid
        self.conn.commit()

        # Verify view aggregation
        self.cursor.execute("""
            SELECT dept_name, total_invoices, total_consultation_revenue, total_pharmacy_revenue, gross_revenue, settled_revenue
            FROM v_daily_department_revenue
            WHERE dept_id = 1;
        """)
        row = self.cursor.fetchone()
        self.assertIsNotNone(row, "View must return aggregated revenue for Department 1")
        self.assertEqual(row[0], "Cardiology")
        self.assertEqual(row[1], 1)
        self.assertEqual(float(row[2]), 200.00)
        self.assertEqual(float(row[3]), 765.00)
        self.assertEqual(float(row[4]), 965.00)
        self.assertEqual(float(row[5]), 965.00)

        # Seed another appointment with a Pending invoice for Doctor 1
        self.cursor.execute(f"""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES ({patient_id}, {doctor_id}, '2026-10-21', '10:00:00', 'Completed');
        """)
        appt2_id = self.cursor.lastrowid
        self.cursor.execute(f"""
            INSERT INTO Invoices (
                appointment_id, patient_id, consultation_fee, medicine_total,
                total_amount, payment_status
            ) VALUES (
                {appt2_id}, {patient_id}, 200.00, 0.00,
                200.00, 'Pending'
            );
        """)
        self.conn.commit()

        # Verify that v_daily_department_revenue filters WHERE payment_status = 'Paid'
        self.cursor.execute("""
            SELECT dept_name, total_invoices, gross_revenue, settled_revenue
            FROM v_daily_department_revenue
            WHERE dept_id = 1;
        """)
        row2 = self.cursor.fetchone()
        self.assertEqual(row2[1], 1, "total_invoices must only count Paid invoices (1, not 2)")
        self.assertEqual(float(row2[2]), 965.00, "gross_revenue must exclude pending invoices")
        self.assertEqual(float(row2[3]), 965.00, "settled_revenue must exclude pending invoices")


class TestPostgresDialectCompliance(unittest.TestCase):
    """
    Empirically inspects PostgreSQL dialect compliance in schema.sql.
    PostgreSQL documentation Section 5.4.1 explicitly states:
    "Check constraints cannot refer to volatile or stable functions (such as current_timestamp, current_date, or random())."
    """

    def test_detect_volatile_functions_in_check_constraints(self):
        with open(SCHEMA_SQL_PATH, "r") as f:
            lines = f.readlines()

        volatile_violations = []
        for idx, line in enumerate(lines):
            # Check for CURRENT_DATE or CURRENT_TIMESTAMP in CHECK constraint definitions
            if ("CURRENT_DATE" in line or "CURRENT_TIMESTAMP" in line) and "CHECK" in line:
                volatile_violations.append((idx + 1, line.strip()))
            elif ("CURRENT_DATE" in line or "CURRENT_TIMESTAMP" in line) and idx > 0 and "CHECK" in lines[idx - 1]:
                volatile_violations.append((idx + 1, line.strip()))

        # Verify that ZERO volatile function violations exist in CHECK constraints per PostgreSQL Section 5.4.1
        self.assertEqual(len(volatile_violations), 0, f"Found prohibited volatile functions in CHECK constraints: {volatile_violations}")

    def test_static_valid_baseline_check_in_patients(self):
        with open(SCHEMA_SQL_PATH, "r") as f:
            sql_content = f.read()

        self.assertIn("chk_patient_dob_valid", sql_content, "chk_patient_dob_valid must be defined in schema.sql")
        self.assertIn("date_of_birth >= '1900-01-01'", sql_content, "chk_patient_dob_valid must enforce date_of_birth >= '1900-01-01'")
        self.assertNotIn("chk_patient_dob_past", sql_content, "Volatile chk_patient_dob_past must not be present in schema.sql")


class TestReschedulingAndCollisionEdgeCases(unittest.TestCase):
    """
    Stress-tests complex concurrency edge cases:
    - Rescheduling collisions vs available slot transitions.
    - Re-activation / un-cancellation collision prevention.
    - Duplicate medicine items in prescriptions.
    """

    def setUp(self):
        self.conn = get_sqlite_connection()
        setup_test_database(self.conn)
        self.cursor = self.conn.cursor()

        self.cursor.execute("INSERT INTO Departments (dept_name, location) VALUES ('Orthopedics', 'Fl 4');")
        self.cursor.execute("""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES (1, 'James', 'Wilson', 'james.wilson@clinic.org', '555-7701', 'Orthopedic Surgeon', 'MD', 220.00);
        """)
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
            VALUES ('Alice', 'Brown', '1988-03-15', 'Female', '555-7702', '12 Lakeview');
        """)
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
            VALUES ('Bob', 'Taylor', '1991-07-22', 'Male', '555-7703', '34 Lakeview');
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_rescheduling_to_occupied_slot_blocked(self):
        # Alice booked at 09:00:00
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-10-30', '09:00:00', 'Scheduled');
        """)
        appt1_id = self.cursor.lastrowid

        # Bob booked at 09:30:00
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (2, 1, '2026-10-30', '09:30:00', 'Scheduled');
        """)
        appt2_id = self.cursor.lastrowid
        self.conn.commit()

        # Alice attempts to reschedule to 09:30:00 (Bob's occupied slot)
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.cursor.execute(f"""
                UPDATE Appointments 
                SET appointment_time = '09:30:00' 
                WHERE appointment_id = {appt1_id};
            """)
        self.assertIn("UNIQUE constraint failed", str(ctx.exception))

        # Alice reschedules to 10:00:00 (unoccupied slot) -> SUCCEEDS
        self.cursor.execute(f"""
            UPDATE Appointments 
            SET appointment_time = '10:00:00' 
            WHERE appointment_id = {appt1_id};
        """)
        self.conn.commit()

        self.cursor.execute(f"SELECT appointment_time FROM Appointments WHERE appointment_id = {appt1_id};")
        self.assertEqual(self.cursor.fetchone()[0], "10:00:00")

    def test_un_cancelling_into_occupied_slot_blocked(self):
        # 1. Alice books 09:00:00
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-10-30', '09:00:00', 'Scheduled');
        """)
        appt1_id = self.cursor.lastrowid
        self.conn.commit()

        # 2. Alice cancels
        self.cursor.execute(f"UPDATE Appointments SET status = 'Cancelled' WHERE appointment_id = {appt1_id};")
        self.conn.commit()

        # 3. Bob books the freed 09:00:00 slot
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (2, 1, '2026-10-30', '09:00:00', 'Scheduled');
        """)
        self.conn.commit()

        # 4. Clinic staff attempts to un-cancel Alice's appointment back to 'Scheduled'
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.cursor.execute(f"""
                UPDATE Appointments 
                SET status = 'Scheduled' 
                WHERE appointment_id = {appt1_id};
            """)
        self.assertIn("UNIQUE constraint failed", str(ctx.exception))

    def test_duplicate_medicine_item_blocked(self):
        # Seed prescription and medicine
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-10-30', '11:00:00', 'Completed');
        """)
        appt_id = self.cursor.lastrowid
        self.cursor.execute(f"""
            INSERT INTO Prescriptions (appointment_id, patient_id, doctor_id, instructions)
            VALUES ({appt_id}, 1, 1, 'Follow dosing instructions');
        """)
        presc_id = self.cursor.lastrowid
        self.cursor.execute("""
            INSERT INTO Medicines (name, category, manufacturer, unit_price, stock_quantity, expiry_date)
            VALUES ('Ibuprofen 400mg', 'Analgesic', 'Pfizer', 8.00, 50, '2028-01-01');
        """)
        med_id = self.cursor.lastrowid
        self.conn.commit()

        # Insert first item
        self.cursor.execute(f"""
            INSERT INTO Prescription_Items (prescription_id, medicine_id, dosage, duration_days, quantity)
            VALUES ({presc_id}, {med_id}, '400mg tid', 5, 15);
        """)
        self.conn.commit()

        # Insert duplicate item with same prescription and medicine -> MUST BE BLOCKED by uq_prescription_medicine
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute(f"""
                INSERT INTO Prescription_Items (prescription_id, medicine_id, dosage, duration_days, quantity)
                VALUES ({presc_id}, {med_id}, '400mg extra', 3, 9);
            """)


if __name__ == "__main__":
    unittest.main()

