import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from config import Config
from omr_processor import process_omr
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import io
from utils.otp_generator import generate_otp
from utils.email_service import send_otp_email, send_result_email
import socket
import pytz
from datetime import datetime, timedelta

def get_ist_now():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'student_login'


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

class Student(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=get_ist_now)
    results = db.relationship('Result', backref='student', lazy=True)

class EmailOTP(db.Model):
    __tablename__ = 'email_otps'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), nullable=False)
    otp_code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

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
    result_pdf = db.Column(db.String(255), nullable=True)
    date = db.Column(db.DateTime, default=get_ist_now)

@login_manager.user_loader
def load_user(user_id):
    return Student.query.get(int(user_id))


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

@app.route('/student_register', methods=['GET', 'POST'])
def student_register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        
        existing_student = Student.query.filter_by(email=email).first()
        if existing_student and existing_student.verified:
            flash('Email already registered!', 'danger')
            return redirect(url_for('student_login'))
            
        otp = generate_otp()
        expires = get_ist_now() + timedelta(minutes=5)
        
        try:
            new_otp = EmailOTP(email=email, otp_code=otp, expires_at=expires)
            db.session.add(new_otp)
            
            if not existing_student:
                hashed_pw = generate_password_hash(password)
                new_student = Student(name=name, email=email, password=hashed_pw, verified=False)
                db.session.add(new_student)
            else:
                existing_student.password = generate_password_hash(password)
                
            db.session.commit()
            
            send_otp_email(email, otp)
            session['verify_email'] = email
            flash('An OTP has been sent to your email.', 'info')
            return redirect(url_for('verify_otp'))
        except Exception as e:
            db.session.rollback()
            flash(f'Database exception: {e}', 'danger')
            
    return render_template('student_register.html')

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    if 'verify_email' not in session:
        return redirect(url_for('student_register'))
        
    email = session['verify_email']
        
    if request.method == 'POST':
        otp_entered = request.form['otp']
        
        otp_record = EmailOTP.query.filter_by(email=email, otp_code=otp_entered).filter(EmailOTP.expires_at > get_ist_now()).first()
        
        if otp_record:
            student = Student.query.filter_by(email=email).first()
            if student:
                student.verified = True
                db.session.commit()
                # Clear all otps for this email
                EmailOTP.query.filter_by(email=email).delete()
                db.session.commit()
                session.pop('verify_email', None)
                flash('Email verified successfully! You can now log in.', 'success')
                return redirect(url_for('student_login'))
        else:
            flash('Invalid or expired OTP', 'danger')
            
    return render_template('verify_otp.html', email=email)

@app.route('/student_login', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        student = Student.query.filter_by(email=email).first()
        
        if student and check_password_hash(student.password, password):
            if not student.verified:
                flash('Please verify your email first.', 'warning')
                return redirect(url_for('student_login'))
                
            login_user(student)
            session['role'] = 'student'
            session['student_id'] = student.id
            flash('Logged in successfully.', 'success')
            
            next_url = request.form.get('next') or request.args.get('next')
            if next_url:
                from urllib.parse import urlparse
                # Ensure the url is safe
                if not urlparse(next_url).netloc:
                    return redirect(next_url)
                    
            return redirect(url_for('student_dashboard'))
        else:
            flash('Invalid email or password', 'danger')
            
    return render_template('student_login.html')

@app.route('/logout')
def logout():
    logout_user()
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
    
    results_list = Result.query.all()
    avg_score = 0
    if total_evaluations > 0:
        total_percentage = sum(r.percentage for r in results_list)
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

@app.route('/student/dashboard')
@login_required
def student_dashboard():
    results = Result.query.filter_by(student_id=current_user.id).order_by(Result.date.desc()).all()
    
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
            import time
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
                
                # Generate PDF and notify student
                student = Student.query.get(student_id)
                pdf_filename = f"result_{result_id}_{int(time.time())}.pdf"
                pdf_path = os.path.join(app.config.get('RESULTS_FOLDER', 'static/results'), pdf_filename)
                os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
                
                buffer = io.BytesIO()
                p = canvas.Canvas(buffer, pagesize=letter)
                p.drawString(100, 750, "OMR Sheet Detection and Marking System - Result Report")
                p.drawString(100, 730, f"Student Name: {student.name}")
                p.drawString(100, 710, f"Exam Date: {new_result.date.strftime('%Y-%m-%d %H:%M')}")
                p.drawString(100, 690, f"Marks: {new_result.score} / {new_result.total_questions}")
                p.drawString(100, 670, f"Percentage: {new_result.percentage:.2f}%")
                p.drawString(100, 650, f"Status: {new_result.status}")
                p.showPage()
                p.save()
                
                with open(pdf_path, 'wb') as f:
                    f.write(buffer.getvalue())
                
                new_result.result_pdf = pdf_filename
                db.session.commit()
                
                result_url = url_for('view_result', result_id=result_id, _external=True)
                
                # Replace localhost with actual LAN IP for external access
                from urllib.parse import urlparse
                parsed_url = urlparse(result_url)
                if parsed_url.hostname in ['127.0.0.1', 'localhost', '0.0.0.0']:
                    lan_ip = get_lan_ip()
                    result_url = result_url.replace(parsed_url.hostname, lan_ip)
                
                send_result_email(student.email, student.name, result_url)
                
            except Exception as e:
                db.session.rollback()
                flash(f'Database/PDF failure: {e}', 'danger')
                return redirect(request.url)
                
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
    import time
    filename = f"key_{int(time.time())}_{filename}"
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(upload_path)
    
    # We need a way to process an OMR sheet and EXTRACT the answers without a key.
    # I'll update omr_processor.py to have an extract_answers function.
    
    try:
        from omr_processor import extract_answers
        extracted_answers = extract_answers(upload_path)
        
        # Clear existing keys for this subject
        AnswerKey.query.filter_by(subject_id=subject_id).delete()
        
        for q_num, opt in extracted_answers.items():
            new_key = AnswerKey(subject_id=subject_id, question_number=q_num, correct_option=opt)
            db.session.add(new_key)
            
        db.session.commit()
        flash(f'Answer key extracted and saved for subject!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error extracting key: {str(e)}', 'danger')
        
    return redirect(url_for('admin_dashboard', subject_id=subject_id))


@app.route('/student/result/<int:result_id>')
def view_result(result_id):
    result = Result.query.get_or_404(result_id)
    
    is_admin = session.get('role') == 'admin'
    is_student_owner = current_user.is_authenticated and result.student_id == current_user.id
    
    if not (is_admin or is_student_owner):
        if not current_user.is_authenticated and not is_admin:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for('student_login', next=request.url))
        flash("You are not authorized to view this result.", "danger")
        return redirect(url_for('student_dashboard') if current_user.is_authenticated else url_for('index'))
        
    return render_template('result.html', result=result)

@app.route('/download_pdf/<int:result_id>')
def download_pdf(result_id):
    result = Result.query.get_or_404(result_id)
    
    is_admin = session.get('role') == 'admin'
    is_student_owner = current_user.is_authenticated and result.student_id == current_user.id
    
    if not (is_admin or is_student_owner):
        flash("You are not authorized to download this PDF.", "danger")
        return redirect(url_for('student_dashboard') if current_user.is_authenticated else url_for('index'))
    
    if not result.result_pdf:
        flash("PDF not generated.", "danger")
        return redirect(url_for('view_result', result_id=result_id))
        
    pdf_path = os.path.join(app.config.get('RESULTS_FOLDER', 'static/results'), result.result_pdf)
    if os.path.exists(pdf_path):
        return send_file(pdf_path, as_attachment=True)
    flash("PDF file missing.", "danger")
    return redirect(url_for('view_result', result_id=result_id))

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
