from app import app, db, Student, Admin, AnswerKey, generate_password_hash

    # 1. Create a "General" Subject if not exists
    subject = Subject.query.filter_by(name='General').first()
    if not subject:
        subject = Subject(name='General')
        db.session.add(subject)
        db.session.commit()
    
    # 2. Create a Test Student if not exists
    if not Student.query.filter_by(email='test@student.com').first():
        test_student = Student(
            name='Test Student',
            email='test@student.com',
            password=generate_password_hash('student123')
        )
        db.session.add(test_student)
        print("Created test student: test@student.com / student123")
    
    # 3. Setup a 5-question Answer Key for the General subject
    answers = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'A'}
    for q_no, opt in answers.items():
        key = AnswerKey.query.filter_by(subject_id=subject.id, question_number=q_no).first()
        if not key:
            key = AnswerKey(subject_id=subject.id, question_number=q_no, correct_option=opt)
            db.session.add(key)
        else:
            key.correct_option = opt
    
    db.session.commit()
    print(f"Answer Key set for '{subject.name}' (1:A, 2:B, 3:C, 4:D, 5:A)")
