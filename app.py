import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from config import Config
from omr_processor import process_omr, extract_answers
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import io
import smtplib
from email.message import EmailMessage
import random
import time

app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)

# --- DATABASE MODELS ---

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    answer_keys = db.relationship('AnswerKey', backref='subject_rel', lazy=True, cascade="all, delete-orphan")
    results = db.relationship('Result', backref='subject_rel', lazy=True, cascade="all, delete-orphan")

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    results = db.relationship('Result', backref='student', lazy=True, cascade="all, delete-orphan")

class AnswerKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    question_number = db.Column(db.Integer, nullable=False)
    correct_option = db.Column(db.String(1), nullable=False)
    
    __table_args__ = (db.UniqueConstraint('subject_id', 'question_number', name='uq_subject_question'),)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    uploaded_image = db.Column(db.String(255), nullable=False)
    processed_image = db.Column(db.String(255), nullable=False)
    percentage = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    date = db.Column(db.DateTime, default=db.func.current_timestamp())

# Ensure directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROCESSED_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'jfif'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('index.html')

# --- AUTH ROUTES ---

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        admin = Admin.query.filter_by(username=username).first()
        
        if admin and check_password_hash(admin.password, password):
            session['admin_id'] = admin.id
            session['role'] = 'admin'
            flash('Logged in successfully.', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password', 'danger')
            
    return render_template('admin_login.html')

def send_otp_email(recipient, otp):
    try:
        msg = EmailMessage()
        msg.set_content(f"Your OTP for VoidFill registration is: {otp}\n\nPlease enter this code to verify your account.")
        msg['Subject'] = 'Registration OTP'
        msg['From'] = f"VoidFill <{app.config['MAIL_USERNAME']}>"
        msg['To'] = recipient

        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        if app.config.get('MAIL_USE_TLS'):
            server.starttls()
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

def send_result_email(recipient, student_name, subject_name, percentage, result_link):
    try:
        msg = EmailMessage()
        msg.set_content(
            f"Hello {student_name},\n\n"
            f"Your OMR sheet for the subject '{subject_name}' has been graded!\n\n"
            f"Your Score: {percentage:.2f}%\n\n"
            f"You can view your detailed results and download the PDF report here: {result_link}\n"
            f"(Note: You may be asked to log in first.)\n\n"
            f"Best regards,\nVoidFill OMR System"
        )
        msg['Subject'] = f'Your Results for {subject_name} are ready!'
        msg['From'] = app.config['MAIL_USERNAME']
        msg['To'] = recipient

        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        if app.config.get('MAIL_USE_TLS'):
            server.starttls()
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send result email: {e}")
        return False

@app.route('/student_register', methods=['GET', 'POST'])
def student_register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        
        # Check if email already exists
        existing_student = Student.query.filter_by(email=email).first()
        if existing_student:
            flash('Email is already registered. Please login.', 'warning')
            return redirect(url_for('student_login'))
            
        # Generate OTP
        otp = str(random.randint(100000, 999999))
        
        # Send Email
        success = send_otp_email(email, otp)
        
        if success:
            session['reg_name'] = name
            session['reg_email'] = email
            session['reg_password'] = generate_password_hash(password)
            session['reg_otp'] = otp
            flash('An OTP has been sent to your email. Please verify.', 'info')
            return redirect(url_for('verify_otp'))
        else:
            flash('Failed to send OTP email. Contact admin or check mail configuration in config.py', 'danger')
            
    return render_template('student_register.html')

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    if 'reg_email' not in session:
        return redirect(url_for('student_register'))
        
    if request.method == 'POST':
        user_otp = request.form.get('otp')
        if user_otp == session.get('reg_otp'):
            try:
                new_student = Student(
                    name=session['reg_name'],
                    email=session['reg_email'],
                    password=session['reg_password']
                )
                db.session.add(new_student)
                db.session.commit()
                
                # Clear session
                for key in ['reg_name', 'reg_email', 'reg_password', 'reg_otp']:
                    session.pop(key, None)
                    
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('student_login'))
            except Exception as e:
                db.session.rollback()
                flash(f'Database exception: {e}', 'danger')
        else:
            flash('Invalid OTP. Please try again.', 'danger')
            
    return render_template('verify_otp.html')

@app.route('/student_login', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        student = Student.query.filter_by(email=email).first()
        
        if student and check_password_hash(student.password, password):
            session['student_id'] = student.id
            session['role'] = 'student'
            flash('Logged in successfully.', 'success')
            return redirect(url_for('student_dashboard'))
        else:
            flash('Invalid email or password', 'danger')
            
    return render_template('student_login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('index'))

# --- DASHBOARDS ---

@app.route('/admin_dashboard', methods=['GET', 'POST'])
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))
        
    if request.method == 'POST' and 'question_number' in request.form:
        q_num = int(request.form['question_number'])
        opt = request.form['correct_option']
        subject_id = request.form.get('subject_id')
        
        if not subject_id:
            flash('Please select a subject.', 'danger')
            return redirect(url_for('admin_dashboard'))
            
        try:
            key = AnswerKey.query.filter_by(subject_id=subject_id, question_number=q_num).first()
            if key:
                key.correct_option = opt
            else:
                key = AnswerKey(subject_id=subject_id, question_number=q_num, correct_option=opt)
                db.session.add(key)
            db.session.commit()
            flash('Answer key updated.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", 'danger')
            
    subjects = Subject.query.all()
    selected_subject_id = request.args.get('subject_id', type=int)
    
    keys = []
    if selected_subject_id:
        keys = AnswerKey.query.filter_by(subject_id=selected_subject_id).order_by(AnswerKey.question_number).all()
        
    # Gather Admin Dashboard Metrics
    total_students = Student.query.count()
    total_evaluations = Result.query.count()
    
    results = Result.query.all()
    avg_score = 0
    if total_evaluations > 0:
        total_percentage = sum(r.percentage for r in results)
        avg_score = total_percentage / total_evaluations
        
    return render_template('admin_dashboard.html', 
                            subjects=subjects, 
                            keys=keys, 
                            selected_subject_id=selected_subject_id,
                            total_students=total_students,
                            total_evaluations=total_evaluations,
                            avg_score=avg_score)

@app.route('/admin/answer_key')
def admin_answer_key():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add_subject', methods=['POST'])
def add_subject():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))
    
    name = request.form.get('subject_name')
    if name:
        try:
            new_subject = Subject(name=name)
            db.session.add(new_subject)
            db.session.commit()
            flash(f'Subject "{name}" added.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding subject: {e}', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/student_dashboard')
def student_dashboard():
    if session.get('role') != 'student':
        return redirect(url_for('student_login'))
        
    results = Result.query.filter_by(student_id=session['student_id']).order_by(Result.date.desc()).all()
    
    total_exams = len(results)
    avg_score = 0
    if total_exams > 0:
        avg_score = sum(r.percentage for r in results) / total_exams
        
    passed_exams = sum(1 for r in results if r.status == 'Pass')
        
    return render_template('student_dashboard.html', 
                            results=results,
                            total_exams=total_exams,
                            avg_score=avg_score,
                            passed_exams=passed_exams)

@app.route('/admin/bulk_key', methods=['POST'])
def bulk_answer_key():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))
    
    subject_id = request.form.get('subject_id')
    if not subject_id:
        flash('Subject ID missing.', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    bulk_data = request.form.get('bulk_answers', '').upper()
    clean_data = "".join(c for c in bulk_data if c in "ABCD")
    
    if not clean_data:
        flash('No valid answers (ABCD) found in input.', 'warning')
        return redirect(url_for('admin_dashboard', subject_id=subject_id))
    
    try:
        start_q = int(request.form.get('start_question', 1))
        for i, opt in enumerate(clean_data):
            q_num = start_q + i
            key = AnswerKey.query.filter_by(subject_id=subject_id, question_number=q_num).first()
            if key:
                key.correct_option = opt
            else:
                key = AnswerKey(subject_id=subject_id, question_number=q_num, correct_option=opt)
                db.session.add(key)
        
        db.session.commit()
        flash(f'Successfully added/updated {len(clean_data)} answers for Question {start_q} onwards.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
        
    return redirect(url_for('admin_dashboard', subject_id=subject_id))

# --- UPLOAD & PROCESS OMR ---

@app.route('/admin/clear_key', methods=['POST'])
def clear_answer_key():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))
    
    subject_id = request.form.get('subject_id')
    try:
        if subject_id:
            db.session.query(AnswerKey).filter_by(subject_id=subject_id).delete()
            msg = f'All answers for the selected subject have been cleared.'
        else:
            db.session.query(AnswerKey).delete()
            msg = 'All answers for all subjects have been cleared.'
            
        db.session.commit()
        flash(msg, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error clearing answer key: {e}', 'danger')
    return redirect(url_for('admin_dashboard', subject_id=subject_id))
@app.route('/upload_omr', methods=['GET', 'POST'])
def upload_omr():
    if session.get('role') != 'admin':
        flash('Only admins can upload and evaluate OMR sheets.', 'warning')
        return redirect(url_for('admin_login'))
        
    students = Student.query.all()
    subjects = Subject.query.all()
    
    if request.method == 'POST':
        student_id = request.form.get('student_id')
        subject_id = request.form.get('subject_id')
        
        if not student_id or not subject_id:
            flash('Please select both student and subject.', 'danger')
            return redirect(request.url)
            
        if 'omr_image' not in request.files:
            flash('No file part', 'danger')
            return redirect(request.url)
            
        file = request.files['omr_image']
        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)
            
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filename = f"{int(time.time())}_{filename}"
            
            upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(upload_path)
            
            keys = AnswerKey.query.filter_by(subject_id=subject_id).all()
            if not keys:
                flash('Missing answer key for this subject. Contact admin.', 'danger')
                return redirect(request.url)
                
            answer_key = {k.question_number: k.correct_option for k in keys}
            
            processed_filename = f"processed_{filename.rsplit('.', 1)[0]}.jpg"
            processed_path = os.path.join(app.config['PROCESSED_FOLDER'], processed_filename)
            
            try:
                # Reuse process_omr - it doesn't care about subjects, just takes the dict
                score, total_q, selected, final_path = process_omr(upload_path, answer_key, processed_path)
            except Exception as e:
                flash(f'OMR Processing Error: {str(e)}', 'danger')
                return redirect(request.url)
                
            total_questions_in_key = len(answer_key)
            percentage = (score / total_questions_in_key) * 100 if total_questions_in_key > 0 else 0
            status = 'Pass' if percentage >= 40 else 'Fail'
            
            try:
                new_result = Result(
                    student_id=student_id,
                    subject_id=subject_id,
                    score=score,
                    total_questions=total_questions_in_key,
                    uploaded_image=filename,
                    processed_image=processed_filename,
                    percentage=percentage,
                    status=status
                )
                db.session.add(new_result)
                db.session.commit()
                result_id = new_result.id
            except Exception as e:
                db.session.rollback()
                flash(f'Database failure: {e}', 'danger')
                return redirect(request.url)
                
            # Send Email Notification to Student
            student = Student.query.get(student_id)
            subject = Subject.query.get(subject_id)
            if student and subject:
                # generate the full link for the exact result
                result_link = url_for('view_result', result_id=result_id, _external=True)
                send_result_email(student.email, student.name, subject.name, percentage, result_link)
                
            flash(f'Successfully evaluated OMR for {new_result.student.name} ({new_result.subject_rel.name})!', 'success')
            return redirect(url_for('view_result', result_id=result_id))
            
        else:
            flash(f'Invalid image type: {file.filename.rsplit(".", 1)[-1]}.', 'danger')
            
    return render_template('upload_omr.html', students=students, subjects=subjects)

@app.route('/admin/upload_key_image', methods=['POST'])
def upload_key_image():
    if session.get('role') != 'admin':
        return redirect(url_for('admin_login'))
        
    subject_id = request.form.get('subject_id')
    if not subject_id:
        flash('Please select a subject first.', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    if 'key_image' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('admin_dashboard', subject_id=subject_id))
        
    file = request.files['key_image']
    if file.filename == '' or not allowed_file(file.filename):
        flash('Invalid file.', 'danger')
        return redirect(url_for('admin_dashboard', subject_id=subject_id))
        
    filename = secure_filename(file.filename)
    filename = f"key_{int(time.time())}_{filename}"
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(upload_path)
    
    # We need a way to process an OMR sheet and EXTRACT the answers without a key.
    # I'll update omr_processor.py to have an extract_answers function.
    
    try:
        extracted_answers = extract_answers(upload_path)
        
        # Clear existing keys for this subject
        AnswerKey.query.filter_by(subject_id=subject_id).delete()
        
        for q_num, opt in extracted_answers.items():
            if opt is not None:
                new_key = AnswerKey(subject_id=subject_id, question_number=q_num, correct_option=opt)
                db.session.add(new_key)
            
        db.session.commit()
        flash(f'Answer key extracted and saved for subject!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error extracting key: {str(e)}', 'danger')
        
    return redirect(url_for('admin_dashboard', subject_id=subject_id))


@app.route('/result/<int:result_id>')
def view_result(result_id):
    if not session.get('role'):
        return redirect(url_for('index'))
        
    result = Result.query.get_or_404(result_id)
    
    # Check authorization: admin can see all, student only their own
    if session.get('role') == 'student' and result.student_id != session.get('student_id'):
        flash("You are not authorized to view this result.", "danger")
        return redirect(url_for('student_dashboard'))
        
    return render_template('result.html', result=result)

@app.route('/download_pdf/<int:result_id>')
def download_pdf(result_id):
    if session.get('role') not in ['student', 'admin']:
        return redirect(url_for('index'))
        
    if session.get('role') == 'student':
        result = Result.query.filter_by(id=result_id, student_id=session['student_id']).first()
    else:
        result = Result.query.filter_by(id=result_id).first()
    
    if not result:
        flash("Result not found.", "danger")
        return redirect(url_for('student_dashboard'))
        
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.drawString(100, 750, "OMR Sheet Detection and Marking System - Result Report")
    p.drawString(100, 730, f"Student Name: {result.student.name}")
    p.drawString(100, 710, f"Date: {result.date}")
    p.drawString(100, 690, f"Score: {result.score} / {result.total_questions}")
    p.drawString(100, 670, f"Percentage: {result.percentage:.2f}%")
    p.drawString(100, 650, f"Status: {result.status}")
    
    try:
        proc_img_path = os.path.join(app.config['PROCESSED_FOLDER'], result.processed_image)
        if os.path.exists(proc_img_path):
            p.drawImage(proc_img_path, 100, 300, width=400, preserveAspectRatio=True)
    except Exception as e:
        print("Image processing print pdf err:", e)
        pass
        
    p.showPage()
    p.save()
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name=f"result_{result_id}.pdf", mimetype='application/pdf')

# Error Handling
@app.errorhandler(413) # File size exceeded
def request_entity_too_large(error):
    flash('File size exceeded. Maximum allowed is 5MB.', 'danger')
    return redirect(request.referrer or url_for('index')), 413

def future_ai_enhancement():
    pass

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Create default admin if not exists
        if not Admin.query.filter_by(username='admin').first():
            hashed_pw = generate_password_hash('admin123')
            admin = Admin(username='admin', password=hashed_pw)
            db.session.add(admin)
            db.session.commit()
    app.run(host='0.0.0.0', debug=True)
