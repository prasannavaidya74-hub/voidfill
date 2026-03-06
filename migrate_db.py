from app import app, db, Subject, AnswerKey, Student, Admin, Result
import sqlite3
import os

def migrate():
    with app.app_context():
        # Get path to db
        db_path = os.path.join(app.root_path, 'instance', 'omr_system.db')
        if not os.path.exists(db_path):
            print("No database found to migrate. Creating new one...")
            db.create_all()
            return

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if subject table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='subject'")
        if not cursor.fetchone():
            print("Creating subject table...")
            db.create_all() # This creates all tables that don't exist

        # Check if subject_id exists in answer_key
        cursor.execute("PRAGMA table_info(answer_key)")
        columns = [c[1] for c in cursor.fetchall()]
        
        if 'subject_id' not in columns:
            print("Adding subject_id to answer_key table...")
            # Create a default subject for migration
            default_subject = Subject.query.filter_by(name='General').first()
            if not default_subject:
                default_subject = Subject(name='General')
                db.session.add(default_subject)
                db.session.commit()
                print(f"Created default 'General' subject with ID {default_subject.id}")
            
            # Add column with default value
            cursor.execute(f"ALTER TABLE answer_key ADD COLUMN subject_id INTEGER REFERENCES subject(id) DEFAULT {default_subject.id}")
            conn.commit()
            print("Migration complete for answer_key.")
        
        # Check if subject_id exists in result
        cursor.execute("PRAGMA table_info(result)")
        columns = [c[1] for c in cursor.fetchall()]
        if 'subject_id' not in columns:
            print("Adding subject_id to result table...")
            default_subject = Subject.query.filter_by(name='General').first()
            cursor.execute(f"ALTER TABLE result ADD COLUMN subject_id INTEGER REFERENCES subject(id) DEFAULT {default_subject.id}")
            conn.commit()
            print("Migration complete for result.")

        conn.close()
        print("All migrations checked/applied.")

if __name__ == "__main__":
    migrate()
