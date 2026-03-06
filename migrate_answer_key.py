import sqlite3
import os

def migrate_structure():
    db_path = os.path.join(os.getcwd(), 'instance', 'omr_system.db')
    if not os.path.exists(db_path):
        print("Database not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Step 1: Check constraints on answer_key
    cursor.execute("PRAGMA index_list(answer_key)")
    indexes = cursor.fetchall()
    print("Existing indexes on answer_key:", indexes)

    # Step 2: Create a backup of the current data
    cursor.execute("SELECT * FROM answer_key")
    current_data = cursor.fetchall()
    
    # Step 3: Recreate answer_key table without the global UNIQUE constraint on question_number
    # and with a multipart UNIQUE constraint on (subject_id, question_number)
    
    print("Recreating answer_key table...")
    cursor.execute("DROP TABLE IF EXISTS answer_key_old")
    cursor.execute("ALTER TABLE answer_key RENAME TO answer_key_old")
    
    cursor.execute("""
    CREATE TABLE answer_key (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER NOT NULL,
        question_number INTEGER NOT NULL,
        correct_option VARCHAR(1) NOT NULL,
        FOREIGN KEY (subject_id) REFERENCES subject (id),
        UNIQUE (subject_id, question_number)
    )
    """)
    
    # Step 4: Map columns and restore data
    # (id, subject_id, question_number, correct_option)
    # Old table structure might vary based on when it was created.
    # Let's check headers.
    cursor.execute("PRAGMA table_info(answer_key_old)")
    old_columns = [c[1] for c in cursor.fetchall()]
    print("Old columns:", old_columns)
    
    # Construct INSERT statement based on available columns
    cols_to_copy = []
    if 'id' in old_columns: cols_to_copy.append('id')
    if 'subject_id' in old_columns: cols_to_copy.append('subject_id')
    if 'question_number' in old_columns: cols_to_copy.append('question_number')
    if 'correct_option' in old_columns: cols_to_copy.append('correct_option')
    
    col_str = ", ".join(cols_to_copy)
    cursor.execute(f"INSERT INTO answer_key ({col_str}) SELECT {col_str} FROM answer_key_old")
    
    cursor.execute("DROP TABLE answer_key_old")
    
    # Step 5: Check result table as well just in case
    # If it was created from the SQL file, it might not have proper subject keys either.
    # But usually, the error is specifically about unique keys in answer_key.

    conn.commit()
    conn.close()
    print("AnswerKey table migration successful. Global UNIQUE constraint removed.")

if __name__ == "__main__":
    migrate_structure()
