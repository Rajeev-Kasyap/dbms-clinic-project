# DBMS Project Plan: MedConnect (Clinic Appointment Management System)

## 1. Mid-Sem Submission Plan (Deadline: Oct 11)
**Objective:** Finalize database schema, document the design, define application workflows, and set up the repository.

### Step 1.1: Database Schema & Design
We are building a 10-table schema to strictly meet the 8-12 tables requirement.

**Proposed Tables:**
1. `Departments`: (dept_id, name, location)
2. `Doctors`: (doctor_id, name, specialization, dept_id, phone, email)
3. `Doctor_Schedules`: (schedule_id, doctor_id, day_of_week, start_time, end_time)
4. `Patients`: (patient_id, name, dob, gender, phone, email, address)
5. `Appointments`: (appointment_id, patient_id, doctor_id, appointment_date, time_slot, status)
6. `Medical_Records`: (record_id, patient_id, doctor_id, diagnosis, treatment, record_date)
7. `Prescriptions`: (prescription_id, appointment_id, issue_date, notes)
8. `Medicines`: (medicine_id, name, manufacturer, unit_price, stock_quantity)
9. `Prescription_Items`: (prescription_item_id, prescription_id, medicine_id, dosage, duration, quantity)
10. `Invoices`: (invoice_id, patient_id, appointment_id, total_amount, payment_status, issued_at)

### Step 1.2: The 5-Page Mid-Sem Design Document (PDF)
Draft the document to include:
- **Page 1: Problem Statement & Requirements:** Description of the clinic system, intended users (Admin, Doctor, Patient), and business rules (e.g., a doctor cannot be double-booked).
- **Page 2: ER/EER Diagram:** Visual representation mapping Entities, Attributes, and Cardinalities (e.g., Patient (1) to (N) Appointments).
- **Page 3: Relational Schema & Keys:** The tables listed above with Primary Keys (PK) underlined and Foreign Keys (FK) identified.
- **Page 4: Integrity Constraints:** `NOT NULL` for critical fields, `CHECK` constraints (e.g., `status IN ('Scheduled', 'Completed', 'Cancelled')`), and `UNIQUE` constraints (e.g., email, phone).
- **Page 5: Normalization & Assumptions:** Justify why the schema is in 3NF/BCNF (no transitive dependencies, every non-prime attribute depends fully on the candidate key).

### Step 1.3: Application Design & Workflows
Three core workflows for the application:
- **Workflow 1 (Booking & Concurrency):** Patient views available slots and books an appointment. A database transaction ensures no double-booking race conditions.
- **Workflow 2 (Prescription & Inventory):** Doctor issues a prescription. A database trigger automatically deducts the prescribed quantity from the `Medicines` table stock.
- **Workflow 3 (Billing & Dashboard):** Admin retrieves a complex aggregated report of daily revenue, categorized by department, using SQL JOINs and Aggregations.

**Technology Stack:**
- **Database:** PostgreSQL (Ideal for complex triggers and constraints).
- **Backend:** FastAPI (Python) or Express.js (Node).
- **Frontend:** React.js / HTML+Tailwind CSS.

---

## 2. Final Project Plan (Deadline: Nov 15)

### Phase 1: Database Implementation (Oct 12 - Oct 25)
- Write `schema.sql` with full DDL.
- Write `seed.sql` generating at least 500 rows using a Python Faker script.
- Implement DBMS Features:
  - 1 Trigger (Inventory management).
  - 1 Stored Procedure (Automated invoicing).
  - 1 View (Doctor-Patient comprehensive view).
- Write the 5 complex SQL queries for reporting.

### Phase 2: Application Development (Oct 26 - Nov 5)
- Develop REST APIs for the 3 core workflows.
- Connect API to PostgreSQL ensuring explicit SQL queries are executed and parameterized (avoid heavy ORMs to demonstrate DBMS knowledge).

### Phase 3: Frontend & Final Polish (Nov 6 - Nov 12)
- Build UI dashboard.
- Connect Frontend to Backend.
- Test workflows end-to-end.
- Prepare `README.md` with run instructions.
- Update the 5-page PDF with final schema changes.
