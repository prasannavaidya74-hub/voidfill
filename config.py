import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'super_secret_key_void_fill'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///omr_system.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join('static', 'uploads')
    PROCESSED_FOLDER = os.path.join('static', 'processed')
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024 # 5 MB limits
