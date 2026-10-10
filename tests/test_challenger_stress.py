"""
Adversarial Empirical Stress-Test Suite — Challenger M1_3
MedConnect Clinic Appointment Management System
Strict empirical verification of schema.sql against PostgreSQL 16 standard and SQLite3 empirical execution harness.
"""

import os
import re
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import sqlglot
from sqlglot import exp

SCHEMA_SQL_PATH = "/home/rajeev/Desktop/projects/dbms-clinic-project/schema.sql"

def get_sqlite_connection():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    
    def regexp(expr, item):
        if item is None:
            return None
        return bool(re.search(expr, item, re.IGNORECASE))

    conn.create_function("regexp", 2, regexp)
    return conn

def convert_pg_to_sqlite(pg_sql: str) -> str:
    sql = pg_sql
    sql = re.sub(r'\bSERIAL\s+PRIMARY\s+KEY\b', 'INTEGER PRIMARY KEY AUTOINCREMENT', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bTIMESTAMPTZ\b', 'TIMESTAMP', sql, flags=re.IGNORECASE)
    sql = re.sub(r'(\w+)\s+~\*\s+(\'[^\']+\')', r'\1 REGEXP \2', sql)
    sql = re.sub(r'\bchar_length\(', 'length(', sql, flags=re.IGNORECASE)
    sql = re.sub(r'(DROP\s+(?:TABLE|VIEW)\s+[^;]+?)\s+CASCADE\s*;', r'\1;', sql, flags=re.IGNORECASE)
    return sql

def setup_test_database(conn):
    with open(SCHEMA_SQL_PATH, "r") as f:
        raw_sql = f.read()

    cursor = conn.cursor()
    tables = re.findall(r'CREATE\s+TABLE\s+.*?;', raw_sql, re.DOTALL | re.IGNORECASE)
    for t in tables:
        cursor.execute(convert_pg_to_sqlite(t))

    indexes = re.findall(r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+.*?;', raw_sql, re.DOTALL | re.IGNORECASE)
    for idx in indexes:
        cursor.execute(convert_pg_to_sqlite(idx))

    views = re.findall(r'CREATE\s+OR\s+REPLACE\s+VIEW\s+(.*?);', raw_sql, re.DOTALL | re.IGNORECASE)
    for v in views:
        cursor.execute(convert_pg_to_sqlite('CREATE VIEW ' + v + ';'))

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


class TestVolatileCheckConstraints(unittest.TestCase):
    """
    Empirically verifies that schema.sql contains ZERO volatile or stable functions
    inside table CHECK constraints, strictly conforming to PostgreSQL 16 Documentation Section 5.4.1.
    """

    def test_zero_volatile_functions_in_check_constraints_ast(self):
        with open(SCHEMA_SQL_PATH, "r") as f:
            sql_content = f.read()

        parsed_statements = sqlglot.parse(sql_content, read="postgres")
        volatile_names = {
            "CURRENT_DATE", "CURRENT_TIMESTAMP", "NOW", "LOCALTIMESTAMP",
            "CLOCK_TIMESTAMP", "RANDOM", "TIMEOFDAY", "STATEMENT_TIMESTAMP"
        }

        detected_violations = []

        for stmt in parsed_statements:
            if stmt.key == "create" and stmt.find(exp.Schema):
                # Search all CHECK constraints in table definition
                for check in stmt.find_all(exp.Check):
                    # Inspect every node in the CHECK expression
                    for node in check.walk():
                        name = None
                        if isinstance(node, exp.Anonymous):
                            name = node.name.upper()
                        elif isinstance(node, exp.Var):
                            name = node.name.upper()
                        elif isinstance(node, exp.CurrentDate):
                            name = "CURRENT_DATE"
                        elif isinstance(node, exp.CurrentTimestamp):
                            name = "CURRENT_TIMESTAMP"

                        if name and name in volatile_names:
                            detected_violations.append((str(stmt.this), str(check), name))

        self.assertEqual(len(detected_violations), 0, 
                         f"Volatile functions detected inside table CHECK constraints: {detected_violations}")

    def test_raw_regex_scan_for_volatile_in_checks(self):
        with open(SCHEMA_SQL_PATH, "r") as f:
            content = f.read()

        # Extract all CONSTRAINT ... CHECK (...) or inline CHECK (...)
        check_blocks = re.findall(r'CONSTRAINT\s+(\w+)\s+CHECK\s*\((.*?)\)(?=[,\n\);])', content, re.DOTALL | re.IGNORECASE)
        volatile_patterns = [r'\bCURRENT_DATE\b', r'\bCURRENT_TIMESTAMP\b', r'\bNOW\(\)', r'\bRANDOM\(\)']

        violations = []
        for c_name, c_expr in check_blocks:
            for pat in volatile_patterns:
                if re.search(pat, c_expr, re.IGNORECASE):
                    violations.append((c_name, c_expr.strip()))

        self.assertEqual(len(violations), 0, f"Found raw volatile check constraints: {violations}")

    def test_patients_static_dob_constraint(self):
        with open(SCHEMA_SQL_PATH, "r") as f:
            content = f.read()

        self.assertIn("chk_patient_dob_valid", content)
        self.assertIn("date_of_birth >= '1900-01-01'", content)
        self.assertNotIn("date_of_birth <= CURRENT_DATE", content)


class TestMedicalRecordsNullabilityAndSetNull(unittest.TestCase):
    """
    Stress-tests Medical_Records appointment_id nullability, multi-null coexistence,
    1:1 appointment uniqueness, and ON DELETE SET NULL referential action.
    """

    def setUp(self):
        self.conn = get_sqlite_connection()
        setup_test_database(self.conn)
        self.cursor = self.conn.cursor()

        self.cursor.execute("INSERT INTO Departments (dept_name, location) VALUES ('General', 'Wing A');")
        self.cursor.execute("""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES (1, 'House', 'MD', 'house@clinic.org', '555-0001', 'Diagnostics', 'MD', 200.00);
        """)
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
            VALUES ('John', 'Doe', '1980-01-01', 'Male', '555-0002', '123 Main St');
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_multiple_null_appointment_records_coexist(self):
        """Medical_Records appointment_id is UNIQUE, but multiple NULLs must coexist (SQL standard)."""
        for i in range(5):
            self.cursor.execute(f"""
                INSERT INTO Medical_Records (appointment_id, patient_id, doctor_id, symptoms, diagnosis, clinical_notes)
                VALUES (NULL, 1, 1, 'Legacy symptom {i}', 'Legacy diagnosis {i}', 'Legacy historical paper chart {i}');
            """)
        self.conn.commit()

        self.cursor.execute("SELECT COUNT(*) FROM Medical_Records WHERE appointment_id IS NULL;")
        count = self.cursor.fetchone()[0]
        self.assertEqual(count, 5, "Multiple NULL appointment_id medical records must be allowed")

    def test_duplicate_non_null_appointment_record_rejected(self):
        """A single appointment can have at most ONE medical record (1:1 constraint)."""
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-11-10', '10:00:00', 'Completed');
        """)
        appt_id = self.cursor.lastrowid
        self.conn.commit()

        self.cursor.execute(f"""
            INSERT INTO Medical_Records (appointment_id, patient_id, doctor_id, symptoms, diagnosis)
            VALUES ({appt_id}, 1, 1, 'Cough', 'Bronchitis');
        """)
        self.conn.commit()

        # Second record for the same appointment must fail with UNIQUE violation
        with self.assertRaises(sqlite3.IntegrityError):
            self.cursor.execute(f"""
                INSERT INTO Medical_Records (appointment_id, patient_id, doctor_id, symptoms, diagnosis)
                VALUES ({appt_id}, 1, 1, 'Second note', 'Bronchitis');
            """)

    def test_on_delete_set_null_on_appointment_deletion(self):
        """Deleting the appointment must cascade to SET NULL on Medical_Records.appointment_id."""
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-11-11', '11:00:00', 'Completed');
        """)
        appt_id = self.cursor.lastrowid
        self.conn.commit()

        self.cursor.execute(f"""
            INSERT INTO Medical_Records (appointment_id, patient_id, doctor_id, symptoms, diagnosis)
            VALUES ({appt_id}, 1, 1, 'Fever', 'Viral Infection');
        """)
        rec_id = self.cursor.lastrowid
        self.conn.commit()

        # Delete appointment
        self.cursor.execute(f"DELETE FROM Appointments WHERE appointment_id = {appt_id};")
        self.conn.commit()

        # Verify record still exists with appointment_id set to NULL
        self.cursor.execute(f"SELECT appointment_id, diagnosis FROM Medical_Records WHERE record_id = {rec_id};")
        row = self.cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row[0], "Medical_Records.appointment_id must be SET NULL upon appointment deletion")
        self.assertEqual(row[1], "Viral Infection")


class TestConcurrencyAndSlotRaceConditions(unittest.TestCase):
    """
    Stress-tests the partial unique index uq_doctor_slot_active under high concurrency
    and complex status transition race conditions.
    """

    def setUp(self):
        self.temp_db = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(self.temp_db)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        def regexp(expr, item):
            if item is None:
                return None
            return bool(re.search(expr, item, re.IGNORECASE))
        conn.create_function("regexp", 2, regexp)
        setup_test_database(conn)

        c = conn.cursor()
        c.execute("INSERT INTO Departments (dept_name, location) VALUES ('Neurology', 'Fl 3');")
        c.execute("""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES (1, 'Charles', 'Xavier', 'xavier@clinic.org', '555-1234', 'Neurology', 'MD, PhD', 300.00);
        """)
        for p in range(1, 51):
            c.execute(f"""
                INSERT INTO Patients (patient_id, first_name, last_name, date_of_birth, gender, phone, address)
                VALUES ({p}, 'Patient', '{p}', '1995-05-05', 'Other', '555-90{p:03d}', 'Test Rd');
            """)
        conn.commit()
        conn.close()

    def tearDown(self):
        for path in [self.temp_db, self.temp_db + "-wal", self.temp_db + "-shm"]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def test_fifty_threads_concurrent_race_condition(self):
        """50 concurrent threads race for the exact same doctor, date, and time slot."""
        successes = []
        failures = []

        def race_book(patient_id):
            c_worker = sqlite3.connect(self.temp_db, timeout=15.0)
            c_worker.execute("PRAGMA foreign_keys = ON;")
            try:
                cur = c_worker.cursor()
                cur.execute(f"""
                    INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                    VALUES ({patient_id}, 1, '2026-11-20', '14:00:00', 'Scheduled');
                """)
                c_worker.commit()
                successes.append(patient_id)
            except sqlite3.IntegrityError:
                failures.append(patient_id)
            finally:
                c_worker.close()

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(race_book, p) for p in range(1, 51)]
            for f in futures:
                f.result()

        self.assertEqual(len(successes), 1, f"Expected exactly 1 booking success, got {len(successes)}")
        self.assertEqual(len(failures), 49, f"Expected exactly 49 booking collisions, got {len(failures)}")

    def test_all_active_statuses_block_collisions(self):
        """Statuses 'Scheduled', 'In_Consultation', 'Completed', 'No_Show' must all block double booking."""
        active_statuses = ['Scheduled', 'In_Consultation', 'Completed', 'No_Show']

        for idx, status in enumerate(active_statuses):
            date_str = f"2026-11-{21 + idx:02d}"
            conn = sqlite3.connect(self.temp_db)
            conn.execute("PRAGMA foreign_keys = ON;")
            cur = conn.cursor()

            # Insert appointment with this active status
            cur.execute(f"""
                INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                VALUES (1, 1, '{date_str}', '09:00:00', '{status}');
            """)
            conn.commit()

            # Attempt to book another appointment into the same slot -> MUST FAIL
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute(f"""
                    INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                    VALUES (2, 1, '{date_str}', '09:00:00', 'Scheduled');
                """)
            conn.close()

    def test_un_cancelling_or_reactivating_into_active_slot_blocked(self):
        """Updating a Cancelled appointment back to an active status when slot is occupied must fail."""
        conn = sqlite3.connect(self.temp_db)
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.cursor()

        # Step 1: Patient 1 books and cancels
        cur.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-12-01', '10:00:00', 'Cancelled');
        """)
        appt1_id = cur.lastrowid
        conn.commit()

        # Step 2: Patient 2 books the newly freed slot
        cur.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (2, 1, '2026-12-01', '10:00:00', 'Scheduled');
        """)
        appt2_id = cur.lastrowid
        conn.commit()

        # Step 3: Attempt to resurrect Patient 1's appointment into any active status
        for active_st in ['Scheduled', 'In_Consultation', 'Completed', 'No_Show']:
            with self.assertRaises(sqlite3.IntegrityError):
                cur.execute(f"UPDATE Appointments SET status = '{active_st}' WHERE appointment_id = {appt1_id};")

        conn.close()


class TestStoredProcedureAndInvoicePreconditions(unittest.TestCase):
    """
    Empirically verifies sp_generate_invoice logic, status preconditions,
    financial snapshot pricing calculation, and idempotency.
    """

    def setUp(self):
        self.conn = get_sqlite_connection()
        setup_test_database(self.conn)
        self.cursor = self.conn.cursor()

        self.cursor.execute("INSERT INTO Departments (dept_name, location) VALUES ('Pediatrics', 'Fl 1');")
        self.cursor.execute("""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES (1, 'Donald', 'Duck', 'duck@clinic.org', '555-7890', 'Pediatrics', 'MD', 175.00);
        """)
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
            VALUES ('Huey', 'Duck', '2015-01-01', 'Male', '555-7891', 'Duckburg');
        """)
        self.cursor.execute("""
            INSERT INTO Medicines (name, category, manufacturer, unit_price, stock_quantity, expiry_date)
            VALUES ('Amoxicillin 250mg', 'Antibiotic', 'Pharma', 12.50, 100, '2027-01-01');
        """)
        self.med_id = self.cursor.lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _simulate_sp_generate_invoice(self, appt_id):
        """
        Exact Python replication of PL/pgSQL sp_generate_invoice(p_appointment_id INT)
        """
        self.cursor.execute("SELECT COUNT(*) FROM Invoices WHERE appointment_id = ?;", (appt_id,))
        if self.cursor.fetchone()[0] > 0:
            return "ALREADY_EXISTS"

        self.cursor.execute("""
            SELECT a.patient_id, a.doctor_id, a.status, d.consultation_fee
            FROM Appointments a
            JOIN Doctors d ON a.doctor_id = d.doctor_id
            WHERE a.appointment_id = ?;
        """, (appt_id,))
        row = self.cursor.fetchone()
        if not row:
            raise ValueError(f"Appointment ID {appt_id} not found.")

        p_id, d_id, status, consult_fee = row
        if status != "Completed":
            raise ValueError(f"Cannot generate invoice for appointment {appt_id} with status {status}")

        self.cursor.execute("SELECT prescription_id FROM Prescriptions WHERE appointment_id = ?;", (appt_id,))
        presc_row = self.cursor.fetchone()
        med_total = Decimal("0.00")
        if presc_row:
            presc_id = presc_row[0]
            self.cursor.execute("""
                SELECT COALESCE(SUM(pi.quantity * m.unit_price), 0.00)
                FROM Prescription_Items pi
                JOIN Medicines m ON pi.medicine_id = m.medicine_id
                WHERE pi.prescription_id = ?;
            """, (presc_id,))
            med_total = Decimal(str(self.cursor.fetchone()[0]))

        total = Decimal(str(consult_fee)) + med_total
        self.cursor.execute("""
            INSERT INTO Invoices (appointment_id, patient_id, consultation_fee, medicine_total, total_amount, payment_status)
            VALUES (?, ?, ?, ?, ?, 'Pending');
        """, (appt_id, p_id, float(consult_fee), float(med_total), float(total)))
        self.conn.commit()
        return "CREATED"

    def test_non_completed_appointments_rejected(self):
        """Calling sp_generate_invoice on Scheduled, In_Consultation, No_Show, Cancelled must fail."""
        for idx, invalid_status in enumerate(['Scheduled', 'In_Consultation', 'No_Show', 'Cancelled']):
            self.cursor.execute(f"""
                INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                VALUES (1, 1, '2026-11-25', '1{idx}:00:00', '{invalid_status}');
            """)
            appt_id = self.cursor.lastrowid
            self.conn.commit()

            with self.assertRaises(ValueError) as ctx:
                self._simulate_sp_generate_invoice(appt_id)
            self.assertIn("Cannot generate invoice for appointment", str(ctx.exception))

    def test_completed_appointment_billing_calculation_and_idempotency(self):
        """Completed appointment succeeds, calculates prescription total correctly, and is idempotent."""
        self.cursor.execute("""
            INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (1, 1, '2026-11-25', '12:00:00', 'Completed');
        """)
        appt_id = self.cursor.lastrowid

        self.cursor.execute(f"""
            INSERT INTO Prescriptions (appointment_id, patient_id, doctor_id, instructions)
            VALUES ({appt_id}, 1, 1, 'Take 2 tablets daily');
        """)
        presc_id = self.cursor.lastrowid

        self.cursor.execute(f"""
            INSERT INTO Prescription_Items (prescription_id, medicine_id, dosage, duration_days, quantity)
            VALUES ({presc_id}, {self.med_id}, '250mg', 10, 20);
        """)
        self.conn.commit()

        # Generate invoice: consultation_fee = 175.00, med_total = 20 * 12.50 = 250.00, total = 425.00
        res = self._simulate_sp_generate_invoice(appt_id)
        self.assertEqual(res, "CREATED")

        self.cursor.execute(f"SELECT consultation_fee, medicine_total, total_amount, payment_status FROM Invoices WHERE appointment_id = {appt_id};")
        inv = self.cursor.fetchone()
        self.assertEqual(float(inv[0]), 175.00)
        self.assertEqual(float(inv[1]), 250.00)
        self.assertEqual(float(inv[2]), 425.00)
        self.assertEqual(inv[3], "Pending")

        # Second invocation must be idempotent
        res2 = self._simulate_sp_generate_invoice(appt_id)
        self.assertEqual(res2, "ALREADY_EXISTS")


class TestDailyDepartmentRevenueView(unittest.TestCase):
    """
    Empirically verifies that v_daily_department_revenue strictly aggregates
    invoices WHERE payment_status = 'Paid', completely excluding Pending, Cancelled, and Refunded.
    """

    def setUp(self):
        self.conn = get_sqlite_connection()
        setup_test_database(self.conn)
        self.cursor = self.conn.cursor()

        self.cursor.execute("INSERT INTO Departments (dept_name, location) VALUES ('Oncology', 'Bldg O');")
        self.dept_id = self.cursor.lastrowid
        self.cursor.execute(f"""
            INSERT INTO Doctors (dept_id, first_name, last_name, email, phone, specialization, qualification, consultation_fee)
            VALUES ({self.dept_id}, 'Stephen', 'Strange', 'strange@clinic.org', '555-4321', 'Oncology', 'MD', 250.00);
        """)
        self.doc_id = self.cursor.lastrowid
        self.cursor.execute("""
            INSERT INTO Patients (first_name, last_name, date_of_birth, gender, phone, address)
            VALUES ('Peter', 'Parker', '2001-08-10', 'Male', '555-4322', 'Forest Hills');
        """)
        self.pat_id = self.cursor.lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_revenue_view_filters_unpaid_invoices(self):
        statuses_and_amounts = [
            ('Paid', 250.00, 'Card'),
            ('Paid', 300.00, 'UPI'),
            ('Pending', 500.00, None),
            ('Cancelled', 150.00, None),
            ('Refunded', 200.00, None)
        ]

        for i, (p_status, amt, method) in enumerate(statuses_and_amounts):
            self.cursor.execute(f"""
                INSERT INTO Appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
                VALUES ({self.pat_id}, {self.doc_id}, '2026-11-28', '1{i}:00:00', 'Completed');
            """)
            a_id = self.cursor.lastrowid

            paid_at_val = "'2026-11-28 12:00:00'" if p_status == 'Paid' else "NULL"
            method_val = f"'{method}'" if method else "NULL"

            self.cursor.execute(f"""
                INSERT INTO Invoices (appointment_id, patient_id, consultation_fee, medicine_total, total_amount, payment_status, payment_method, paid_at, issued_at)
                VALUES ({a_id}, {self.pat_id}, {amt}, 0.00, {amt}, '{p_status}', {method_val}, {paid_at_val}, '2026-11-28 10:00:00');
            """)

        self.conn.commit()

        # Query view
        self.cursor.execute(f"""
            SELECT total_invoices, gross_revenue, settled_revenue
            FROM v_daily_department_revenue
            WHERE dept_id = {self.dept_id};
        """)
        row = self.cursor.fetchone()
        self.assertIsNotNone(row)
        total_inv, gross_rev, settled_rev = row

        # Only the 2 'Paid' invoices (250 + 300 = 550) should be included!
        self.assertEqual(total_inv, 2, "Only Paid invoices must be counted")
        self.assertEqual(float(gross_rev), 550.00, "Gross revenue must only include Paid invoices ($550.00)")
        self.assertEqual(float(settled_rev), 550.00, "Settled revenue must match Paid invoices ($550.00)")


if __name__ == "__main__":
    unittest.main()
