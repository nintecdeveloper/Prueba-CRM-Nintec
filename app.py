import os
import json
import secrets
from datetime import datetime, date, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import io
import re

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'oslaprint_pro_2026_secure_key')
database_url = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(basedir, 'oslaprint.db'))
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'txt'}

# Crear carpeta de uploads si no existe
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- MODELOS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(100), nullable=False, index=True)  # ✅ Email puede ser compartido entre usuarios
    password_hash = db.Column(db.String(512))
    role = db.Column(db.String(20))  # 'admin' o 'tech'
    reset_token = db.Column(db.String(100), unique=True, nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(100), nullable=True)
    address = db.Column(db.String(250), nullable=True)
    link = db.Column(db.String(500), nullable=True)
    notes = db.Column(db.Text)
    has_support = db.Column(db.Boolean, default=False)
    # ✅ NUEVO: horario de soporte — 'lv'=L-V / 'ls'=L-S / 'ld'=L-D
    support_schedule = db.Column(db.String(5), nullable=True)

class TechProfile(db.Model):
    """Perfil extendido de técnico - datos personales y notas internas del admin"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    user = db.relationship('User', backref=db.backref('profile', uselist=False))
    full_name = db.Column(db.String(150), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    address = db.Column(db.String(250), nullable=True)
    emergency_contact = db.Column(db.String(150), nullable=True)
    emergency_phone = db.Column(db.String(20), nullable=True)
    start_date = db.Column(db.String(10), nullable=True)
    dni = db.Column(db.String(20), nullable=True)
    internal_notes = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.now)

class ServiceType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    color = db.Column(db.String(7), default='#6c757d')
    
    def __repr__(self):
        return f"{self.name}"

class StockCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('stock_category.id'), nullable=True)
    parent = db.relationship('StockCategory', remote_side=[id], backref='subcategories')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('stock_category.id'), nullable=True)
    category = db.relationship('StockCategory', backref='items')
    min_stock = db.Column(db.Integer, default=5)
    description = db.Column(db.Text)
    supplier = db.Column(db.String(100), nullable=True)  # ✅ NUEVO CAMPO PROVEEDOR

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tech_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    client_name = db.Column(db.String(100))
    description = db.Column(db.Text)
    
    date = db.Column(db.Date, nullable=True)
    start_time = db.Column(db.String(10)) 
    end_time = db.Column(db.String(10))   
    
    service_type_id = db.Column(db.Integer, db.ForeignKey('service_type.id'))
    parts_text = db.Column(db.String(200))  
    
    stock_item_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=True)
    stock_quantity_used = db.Column(db.Integer, default=0)
    stock_action = db.Column(db.String(20))

    status = db.Column(db.String(20), default='Pendiente')  # Pendiente, Completado o Sin asignar
    
    # ✅ NUEVO: Campos para asistencia remota
    is_remote = db.Column(db.Boolean, default=False)  # Es asistencia remota
    remote_support_hours = db.Column(db.Float, default=0)  # Horas de soporte registradas
    
    # Campos para firma digital
    signature_data = db.Column(db.Text)
    signature_client_name = db.Column(db.String(100))
    signature_timestamp = db.Column(db.DateTime)
    
    # Archivos adjuntos
    attachments = db.Column(db.Text)  # JSON
    
    # Cronómetro de parte (solo inicio y fin)
    work_start_time = db.Column(db.DateTime)
    work_end_time = db.Column(db.DateTime)
    # Duración total medida por el cronómetro del técnico (formato HH:MM:SS)
    work_duration = db.Column(db.String(20), nullable=True)

    # ✅ Timestamps del parte v2 (HH:MM registrado al pulsar botón)
    parte_transport_start = db.Column(db.String(10), nullable=True)  # Inicio transporte
    parte_arrival         = db.Column(db.String(10), nullable=True)  # Hora llegada
    parte_work_start      = db.Column(db.String(10), nullable=True)  # Inicio trabajo
    parte_work_end        = db.Column(db.String(10), nullable=True)  # Fin trabajo

    # ✅ NUEVO: Campo para registrar quién creó la tarea (admin que la agendó)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    tech = db.relationship('User', foreign_keys=[tech_id], backref='tasks')
    client = db.relationship('Client', backref='tasks')
    service_type = db.relationship('ServiceType', backref='tasks')
    stock_item = db.relationship('Stock', backref='tasks')
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_tasks')

class TaskTechnician(db.Model):
    """Tabla auxiliar para múltiples técnicos en una misma cita"""
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    task = db.relationship('Task', backref=db.backref('extra_technicians', cascade='all, delete-orphan'))
    user = db.relationship('User', backref='extra_tasks')

class Alarm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alarm_type = db.Column(db.String(50))
    title = db.Column(db.String(100))
    description = db.Column(db.Text)
    client_name = db.Column(db.String(100), nullable=True)
    stock_item_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_read = db.Column(db.Boolean, default=False)
    priority = db.Column(db.String(20), default='normal')

class ClientPayment(db.Model):
    """Registro de pago principal asociado a un cliente"""
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, unique=True)
    client = db.relationship('Client', backref=db.backref('payment', uselist=False))
    total_amount = db.Column(db.Float, default=0.0)
    budget_number = db.Column(db.String(50), nullable=True)
    first_payment = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now)

class PaymentRecord(db.Model):
    """Cobro parcial registrado para un cliente"""
    id = db.Column(db.Integer, primary_key=True)
    client_payment_id = db.Column(db.Integer, db.ForeignKey('client_payment.id'), nullable=False)
    client_payment = db.relationship('ClientPayment', backref='records')
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=date.today)
    notes = db.Column(db.Text, nullable=True)
    is_paid = db.Column(db.Boolean, default=False, nullable=False)  # ✅ Estado manual del cobro
    created_at = db.Column(db.DateTime, default=datetime.now)

class TimerSession(db.Model):
    """✅ NUEVO: Sesión de cronómetro persistente para técnicos"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=True)
    timer_type = db.Column(db.String(20))  # 'work', 'travel', 'remote'
    start_time = db.Column(db.DateTime, default=datetime.now)
    elapsed_seconds = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User', backref='timer_sessions')
    task = db.relationship('Task', backref='timer_sessions')

@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))

# --- FUNCIONES AUXILIARES ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def validate_password(password):
    """Validar contraseña con requisitos de seguridad"""
    if len(password) < 6:
        return False, "La contraseña debe tener al menos 6 caracteres"
    if not re.search(r'[A-Z]', password):
        return False, "La contraseña debe contener al menos una mayúscula"
    if not re.search(r'[a-z]', password):
        return False, "La contraseña debe contener al menos una minúscula"
    if not re.search(r'[0-9]', password):
        return False, "La contraseña debe contener al menos un número"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "La contraseña debe contener al menos un carácter especial"
    return True, "Contraseña válida"

def validate_email(email):
    """Validar formato de email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def check_low_stock():
    """Verificar stock bajo y crear alarmas"""
    try:
        low_items = Stock.query.filter(Stock.quantity <= Stock.min_stock).all()
        for item in low_items:
            existing = Alarm.query.filter_by(
                alarm_type='low_stock',
                stock_item_id=item.id,
                is_read=False
            ).first()
            
            if not existing:
                alarm = Alarm(
                    alarm_type='low_stock',
                    title=f'Stock bajo: {item.name}',
                    description=f'El stock de {item.name} está en {item.quantity} unidades (mínimo: {item.min_stock})',
                    stock_item_id=item.id,
                    priority='high'
                )
                db.session.add(alarm)
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error en check_low_stock: {str(e)}")

# --- CONTEXT PROCESSOR ---
@app.context_processor
def inject_globals():
    try:
        unread_alarms = 0
        employees = []
        
        try:
            if current_user.is_authenticated and current_user.role == 'admin':
                unread_alarms = Alarm.query.filter_by(is_read=False).count()
                employees = User.query.filter_by(role='tech').all()
        except Exception as e:
            print(f"Error en context_processor: {e}")
            unread_alarms = 0
            employees = []
        
        return {
            'all_service_types': ServiceType.query.order_by(ServiceType.name).all() if ServiceType.query.count() > 0 else [],
            'unread_alarms_count': unread_alarms,
            'employees': employees,
            'now': datetime.now  # ✅ Añadir función now para templates
        }
    except Exception as e:
        print(f"ERROR context_processor: {e}")
        return {
            'all_service_types': [],
            'unread_alarms_count': 0,
            'employees': [],
            'now': datetime.now
        }

# Filtro Jinja2 para parsear JSON
@app.template_filter('from_json')
def from_json_filter(value):
    """Parsear JSON string a objeto Python"""
    if not value:
        return []
    try:
        return json.loads(value)
    except:
        return []

# --- RUTAS PRINCIPALES ---
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Usuario o contraseña incorrectos', 'danger')
    
    return render_template('login.html')

@app.route('/forgot_password', methods=['POST'])
def forgot_password():
    """Procesar solicitud de recuperación de contraseña"""
    try:
        email = request.form.get('email', '').strip()
        
        if not email:
            flash('Por favor ingresa tu correo electrónico', 'danger')
            return redirect(url_for('login'))
        
        # ✅ MEJORADO: Buscar TODOS los usuarios con este email (puede haber varios)
        users = User.query.filter_by(email=email).all()
        
        if not users:
            # Por seguridad, no revelamos si el email existe o no
            flash('Si el correo existe en nuestro sistema, recibirás un enlace de recuperación', 'info')
            return redirect(url_for('login'))
        
        # ✅ Generar token para CADA usuario con este email
        for user in users:
            reset_token = secrets.token_urlsafe(32)
            user.reset_token = reset_token
            user.reset_token_expiry = datetime.now() + timedelta(hours=24)
            
            # En producción, aquí se enviaría un email con el enlace
            # Por ahora, mostramos el enlace en consola para desarrollo
            reset_link = url_for('reset_password', token=reset_token, _external=True)
            print("\n" + "="*60)
            print("🔐 ENLACE DE RECUPERACIÓN DE CONTRASEÑA")
            print("="*60)
            print(f"Usuario: {user.username} ({user.role})")
            print(f"Email: {user.email}")
            print(f"Enlace: {reset_link}")
            print("="*60 + "\n")
        
        db.session.commit()
        
        num_accounts = len(users)
        if num_accounts > 1:
            flash(f'Se han generado {num_accounts} enlaces de recuperación para las cuentas asociadas a este email. Revisa la consola del servidor.', 'success')
        else:
            flash('Se ha generado un enlace de recuperación. Revisa la consola del servidor.', 'success')
        
        return redirect(url_for('login'))
        
    except Exception as e:
        print(f"Error en forgot_password: {str(e)}")
        flash('Error al procesar la solicitud', 'danger')
        return redirect(url_for('login'))

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Página y procesamiento de restablecimiento de contraseña"""
    # Verificar que el token sea válido
    user = User.query.filter_by(reset_token=token).first()
    
    if not user:
        flash('Token de recuperación inválido', 'danger')
        return redirect(url_for('login'))
    
    # Verificar que el token no haya expirado
    if user.reset_token_expiry < datetime.now():
        flash('El token de recuperación ha expirado', 'danger')
        # Limpiar el token expirado
        user.reset_token = None
        user.reset_token_expiry = None
        db.session.commit()
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        try:
            new_password = request.form.get('password')
            confirm_password = request.form.get('confirm_password')
            
            # Validar que las contraseñas coincidan
            if new_password != confirm_password:
                flash('Las contraseñas no coinciden', 'danger')
                return redirect(url_for('reset_password', token=token))
            
            # Validar requisitos de seguridad
            is_valid, message = validate_password(new_password)
            if not is_valid:
                flash(message, 'danger')
                return redirect(url_for('reset_password', token=token))
            
            # Actualizar contraseña
            user.password_hash = generate_password_hash(new_password)
            
            # Limpiar token
            user.reset_token = None
            user.reset_token_expiry = None
            
            db.session.commit()
            
            flash('✅ Contraseña restablecida correctamente. Ya puedes iniciar sesión.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            print(f"Error resetting password: {str(e)}")
            flash('Error al restablecer la contraseña', 'danger')
            return redirect(url_for('reset_password', token=token))
    
    # Mostrar formulario de reset
    return render_template('reset_password.html', token=token)


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'admin':
        empleados = User.query.filter_by(role='tech').all()
        clients = Client.query.order_by(Client.name).all()
        services = ServiceType.query.all()
        informes = Task.query.filter_by(status='Completado').order_by(Task.date.desc()).limit(50).all()
        stock_items = Stock.query.order_by(Stock.name).all()
        # ✅ Obtener categorías para el panel de stock
        stock_categories = StockCategory.query.filter_by(parent_id=None).all()
        # ✅ NUEVO: todas las categorías (incluidas subcategorías) para el selector de edición
        all_categories = StockCategory.query.order_by(StockCategory.name).all()
        
        return render_template('admin_panel.html', 
                             empleados=empleados,
                             clients=clients,
                             services=services,
                             informes=informes,
                             stock_items=stock_items,
                             stock_categories=stock_categories,
                             all_categories=all_categories,
                             today_date=date.today().strftime('%Y-%m-%d'))
    else:
        # ✅ Solo mostrar tareas pendientes cercanas: desde ayer hasta 3 días adelante
        yesterday = date.today() - timedelta(days=1)
        three_days_ahead = date.today() + timedelta(days=3)
        primary_pending = Task.query.filter(
            Task.tech_id == current_user.id,
            Task.status == 'Pendiente',
            Task.date >= yesterday,
            Task.date <= three_days_ahead
        ).order_by(Task.date.asc()).all()
        
        # Incluir tareas donde es técnico secundario
        extra_task_ids = db.session.query(TaskTechnician.task_id).filter_by(user_id=current_user.id).all()
        extra_task_ids = [r[0] for r in extra_task_ids]
        if extra_task_ids:
            extra_pending = Task.query.filter(
                Task.id.in_(extra_task_ids),
                Task.tech_id != current_user.id,
                Task.status == 'Pendiente',
                Task.date >= yesterday,
                Task.date <= three_days_ahead
            ).order_by(Task.date.asc()).all()
        else:
            extra_pending = []
        
        # Incluir tareas sin técnico asignado (cualquier técnico puede verlas y completarlas)
        unassigned_pending = Task.query.filter(
            Task.tech_id == None,
            Task.status == 'Sin asignar',
            Task.date >= yesterday,
            Task.date <= three_days_ahead
        ).order_by(Task.date.asc()).all()

        # Combinar y ordenar
        pending_tasks_all = sorted(
            primary_pending + extra_pending + unassigned_pending,
            key=lambda t: (t.date, t.start_time or '')
        )
        
        stock_items = Stock.query.filter(Stock.quantity > 0).order_by(Stock.name).all()
        stock_categories = StockCategory.query.filter_by(parent_id=None).order_by(StockCategory.name).all()
        
        return render_template('tech_panel.html',
                             pending_tasks=pending_tasks_all,
                             stock_items=stock_items,
                             stock_categories=stock_categories,
                             today_date=date.today().strftime('%Y-%m-%d'))

@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    try:
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        
        if not check_password_hash(current_user.password_hash, current_password):
            flash('Contraseña actual incorrecta', 'danger')
            return redirect(url_for('dashboard'))
        
        is_valid, message = validate_password(new_password)
        if not is_valid:
            flash(message, 'danger')
            return redirect(url_for('dashboard'))
        
        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        flash('✅ Contraseña actualizada correctamente', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Error changing password: {e}")
        flash('Error al cambiar la contraseña', 'danger')
        return redirect(url_for('dashboard'))

# --- GESTIÓN DE USUARIOS ---
@app.route('/manage_users', methods=['POST'])
@login_required
def manage_users():
    if current_user.role != 'admin':
        flash('No autorizado', 'danger')
        return redirect(url_for('dashboard'))
    
    action = request.form.get('action')
    
    try:
        if action == 'add':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            role = request.form.get('role', 'tech')
            
            # ✅ VALIDACIONES MEJORADAS
            if not username or not email or not password:
                flash('Todos los campos son obligatorios', 'danger')
                return redirect(url_for('dashboard'))
            
            # Validar formato de email
            if not validate_email(email):
                flash('El formato del correo electrónico no es válido', 'danger')
                return redirect(url_for('dashboard'))
            
            # ✅ VALIDACIÓN: Verificar username único
            if User.query.filter_by(username=username).first():
                flash('Ya existe un usuario con ese nombre', 'danger')
                return redirect(url_for('dashboard'))
            
            # ✅ El email puede ser compartido entre usuarios (no se valida unicidad)
            # Validar contraseña
            is_valid, message = validate_password(password)
            if not is_valid:
                flash(message, 'danger')
                return redirect(url_for('dashboard'))
            
            new_user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                role=role
            )
            db.session.add(new_user)
            db.session.commit()
            
            flash(f'Usuario {username} creado correctamente', 'success')
        
        elif action == 'delete':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            
            if user and user.id != current_user.id:
                # ✅ Verificar si tiene tareas asignadas
                if user.tasks:
                    flash(f'No se puede eliminar el usuario porque tiene {len(user.tasks)} tareas asignadas', 'danger')
                    return redirect(url_for('dashboard'))
                
                db.session.delete(user)
                db.session.commit()
                flash('Usuario eliminado correctamente', 'success')
            else:
                flash('No puedes eliminar tu propio usuario', 'danger')
        
        elif action == 'rename':
            user_id = request.form.get('user_id')
            new_username = request.form.get('new_username', '').strip()
            user = User.query.get(user_id)

            if not user:
                flash('Usuario no encontrado', 'danger')
            elif not new_username:
                flash('El nuevo nombre de usuario no puede estar vacío', 'danger')
            elif User.query.filter(User.username == new_username, User.id != user.id).first():
                flash(f'El nombre "{new_username}" ya está en uso por otro usuario', 'danger')
            else:
                old_username = user.username
                user.username = new_username
                db.session.commit()
                flash(f'Usuario "{old_username}" renombrado a "{new_username}" correctamente', 'success')

        else:
            flash('Acción no válida', 'danger')
    
    except IntegrityError as e:
        db.session.rollback()
        print(f"Error de integridad en manage_users: {str(e)}")
        if 'username' in str(e).lower():
            flash('Error: El nombre de usuario ya existe', 'danger')
        else:
            flash('Error de integridad en la base de datos', 'danger')
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error en manage_users: {str(e)}")
        flash('Error al procesar la solicitud', 'danger')
    
    return redirect(url_for('dashboard'))

# --- GESTIÓN DE CLIENTES ---

def _task_duration_minutes(task):
    """Calcula la duración de una tarea en MINUTOS probando todas las fuentes.
    Prioridad: work_duration → remote_support_hours → start/end_time → work_start/end_time
    """
    import re as _re

    wd = (task.work_duration or '').strip()
    if wd:
        # HH:MM:SS o MM:SS
        if wd.count(':') >= 1:
            try:
                parts = wd.split(':')
                if len(parts) == 3:
                    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                elif len(parts) == 2:
                    h, m, s = 0, int(parts[0]), int(parts[1])
                else:
                    raise ValueError
                mins = h * 60 + m + (1 if s >= 30 else 0)
                if mins > 0:
                    return mins
            except Exception:
                pass
        # "Xh YYmin" / "Xh" / "YYmin"
        try:
            h_m = _re.search(r'(\d+)\s*h', wd)
            m_m = _re.search(r'(\d+)\s*min', wd)
            h_val = int(h_m.group(1)) if h_m else 0
            m_val = int(m_m.group(1)) if m_m else 0
            mins = h_val * 60 + m_val
            if mins > 0:
                return mins
        except Exception:
            pass

    rsh = getattr(task, 'remote_support_hours', None) or 0
    if rsh > 0:
        return int(round(rsh * 60))

    if task.start_time and task.end_time:
        try:
            sh, sm = map(int, task.start_time.split(':'))
            eh, em = map(int, task.end_time.split(':'))
            mins = (eh * 60 + em) - (sh * 60 + sm)
            if mins > 0:
                return mins
        except Exception:
            pass

    if task.work_start_time and task.work_end_time:
        try:
            diff = (task.work_end_time - task.work_start_time).total_seconds()
            mins = int(diff / 60)
            if mins > 0:
                return mins
        except Exception:
            pass

    return 0


@app.route('/api/export_clients_csv')
@login_required
def export_clients_csv():
    """Exportar lista de clientes a CSV descargable"""
    if current_user.role != 'admin':
        flash('No autorizado', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        import csv
        from io import StringIO
        
        # Obtener todos los clientes
        clients = Client.query.order_by(Client.name).all()
        
        # Crear CSV en memoria
        output = StringIO()
        writer = csv.writer(output, delimiter=';', lineterminator='\n')
        
        # Encabezados
        writer.writerow([
            'Nombre', 'Teléfono', 'Email', 'Dirección', 
            'Enlace', 'Tiene Soporte', 'Horario Soporte', 'Notas'
        ])
        
        # Datos de clientes
        for client in clients:
            support_schedule_map = {'lv': 'L-V', 'ls': 'L-S', 'ld': 'L-D'}
            schedule_display = support_schedule_map.get(client.support_schedule, '—') if client.has_support else '—'
            
            writer.writerow([
                client.name or '',
                client.phone or '',
                client.email or '',
                client.address or '',
                client.link or '',
                'Sí' if client.has_support else 'No',
                schedule_display,
                client.notes or ''
            ])
        
        # Crear respuesta con descarga
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'clientes_oslaprint_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        print(f"Error exporting clients: {str(e)}")
        flash('Error al exportar clientes', 'danger')
        return redirect(url_for('dashboard'))


@app.route('/manage_clients', methods=['POST'])
@login_required
def manage_clients():
    if current_user.role != 'admin':
        flash('No autorizado', 'danger')
        return redirect(url_for('dashboard'))
    
    action = request.form.get('action')
    
    if action == 'add':
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        address = request.form.get('address', '').strip()
        link = request.form.get('link', '').strip()
        notes = request.form.get('notes', '')
        has_support = request.form.get('has_support') == 'on'
        support_schedule = request.form.get('support_schedule', '').strip() if has_support else None

        # Validaciones básicas
        if not name:
            flash('El nombre del cliente es obligatorio', 'danger')
            return redirect(url_for('dashboard'))
        if not phone:
            flash('El teléfono es obligatorio', 'danger')
            return redirect(url_for('dashboard'))
        if has_support and support_schedule not in ('lv', 'ls', 'ld'):
            support_schedule = 'lv'  # valor por defecto seguro

        if Client.query.filter_by(name=name).first():
            flash('Ya existe un cliente con ese nombre', 'danger')
            return redirect(url_for('dashboard'))
        
        new_client = Client(
            name=name,
            phone=phone,
            email=email,
            address=address,
            link=link,
            notes=notes,
            has_support=has_support,
            support_schedule=support_schedule if has_support else None
        )
        db.session.add(new_client)
        db.session.commit()
        
        flash(f'Cliente {name} añadido correctamente', 'success')
    
    elif action == 'edit':
        client_id = request.form.get('client_id')
        client = Client.query.get(client_id)
        
        if client:
            # Verificar si el nombre está siendo cambiado y si ya existe otro cliente con ese nombre
            new_name = request.form.get('name')
            if new_name != client.name:
                existing_client = Client.query.filter_by(name=new_name).first()
                if existing_client:
                    flash('Ya existe un cliente con ese nombre', 'danger')
                    return redirect(url_for('dashboard'))
            
            client.name = new_name
            client.phone = request.form.get('phone')
            client.email = request.form.get('email')
            client.address = request.form.get('address')
            client.link = request.form.get('link', '')
            client.notes = request.form.get('notes', '')
            has_support_value = request.form.get('has_support')
            client.has_support = (has_support_value == 'on' or has_support_value == 'true')
            # Guardar horario de soporte con validación
            if client.has_support:
                sched = request.form.get('support_schedule', 'lv').strip()
                client.support_schedule = sched if sched in ('lv', 'ls', 'ld') else 'lv'
            else:
                client.support_schedule = None
            
            db.session.commit()
            flash('Cliente actualizado correctamente', 'success')
    
    elif action == 'delete':
        client_id = request.form.get('client_id')
        client = Client.query.get(client_id)
        
        if client:
            try:
                # 1. Nullify client_id in tasks (tasks are kept, just unlinked)
                for task in client.tasks:
                    task.client_id = None
                db.session.flush()
                # 2. Delete payment records then payment
                payment = ClientPayment.query.filter_by(client_id=client.id).first()
                if payment:
                    PaymentRecord.query.filter_by(client_payment_id=payment.id).delete()
                    db.session.delete(payment)
                    db.session.flush()
                # 3. Delete extra technicians on tasks via cascade (task_technician)
                # Already handled by CASCADE in FK definition
                db.session.delete(client)
                db.session.commit()
                flash('Cliente eliminado correctamente', 'success')
            except Exception as e:
                db.session.rollback()
                print(f"Error deleting client: {e}")
                flash(f'Error al eliminar el cliente: {str(e)}', 'danger')
    
    return redirect(url_for('dashboard'))

# --- GESTIÓN DE TIPOS DE SERVICIO ---
@app.route('/manage_services', methods=['POST'])
@login_required
def manage_services():
    if current_user.role != 'admin':
        flash('No autorizado', 'danger')
        return redirect(url_for('dashboard'))
    
    action = request.form.get('action')
    
    if action == 'add':
        name = request.form.get('name')
        color = request.form.get('color', '#6c757d')
        
        if ServiceType.query.filter_by(name=name).first():
            flash('Ya existe un tipo de servicio con ese nombre', 'danger')
            return redirect(url_for('dashboard'))
        
        new_service = ServiceType(name=name, color=color)
        db.session.add(new_service)
        db.session.commit()
        
        flash(f'Tipo de servicio {name} añadido correctamente', 'success')
    
    elif action == 'delete':
        service_id = request.form.get('service_id')
        service = ServiceType.query.get(service_id)
        
        if service:
            db.session.delete(service)
            db.session.commit()
            flash('Tipo de servicio eliminado correctamente', 'success')
    
    return redirect(url_for('dashboard'))

# --- GESTIÓN DE STOCK ---
@app.route('/manage_stock', methods=['POST'])
@login_required
def manage_stock():
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    action = request.form.get('action')
    
    if action == 'add':
        try:
            name = request.form.get('name', '').strip()
            if not name:
                return jsonify({'success': False, 'msg': 'El nombre del producto es obligatorio'})

            # ✅ CORRECCIÓN CRÍTICA: usar subcategory_id si está seleccionado, si no usar category_id
            subcategory_id = request.form.get('subcategory_id', '').strip()
            category_id    = request.form.get('category_id', '').strip()
            final_category_id = subcategory_id if subcategory_id else category_id

            # Validar que la categoría exista (si se proporcionó)
            if final_category_id:
                cat = StockCategory.query.get(int(final_category_id))
                if not cat:
                    return jsonify({'success': False, 'msg': 'La categoría seleccionada no existe'})

            # ✅ CORRECCIÓN: int() dentro de try para evitar crash con valores inválidos
            try:
                quantity  = int(request.form.get('quantity', 0))
                min_stock = int(request.form.get('min_stock', 5))
            except (ValueError, TypeError):
                return jsonify({'success': False, 'msg': 'Los campos de cantidad deben ser números enteros'})

            if quantity < 0:
                return jsonify({'success': False, 'msg': 'La cantidad no puede ser negativa'})
            if min_stock < 0:
                return jsonify({'success': False, 'msg': 'El stock mínimo no puede ser negativo'})

            supplier    = request.form.get('supplier', '').strip()
            description = request.form.get('description', '').strip()

            new_item = Stock(
                name=name,
                category_id=int(final_category_id) if final_category_id else None,
                quantity=quantity,
                min_stock=min_stock,
                supplier=supplier,
                description=description
            )
            db.session.add(new_item)
            db.session.commit()
            check_low_stock()

            return jsonify({'success': True, 'msg': f'Producto "{name}" añadido correctamente'})

        except SQLAlchemyError as e:
            db.session.rollback()
            print(f"Error SQLAlchemy en manage_stock add: {str(e)}")
            return jsonify({'success': False, 'msg': 'Error al guardar en la base de datos'})
        except Exception as e:
            db.session.rollback()
            print(f"Error inesperado en manage_stock add: {str(e)}")
            return jsonify({'success': False, 'msg': f'Error inesperado: {str(e)}'})
    
    elif action == 'edit':  # ✅ NUEVA ACCIÓN PARA EDITAR
        item_id = request.form.get('item_id')
        item = Stock.query.get(item_id)
        
        if item:
            item.name = request.form.get('name')
            item.min_stock = int(request.form.get('min_stock', 5))
            item.supplier = request.form.get('supplier', '')
            item.category_id = int(request.form.get('category_id')) if request.form.get('category_id') else None
            
            db.session.commit()
            check_low_stock()
            return jsonify({'success': True, 'msg': 'Artículo actualizado correctamente'})
        
        return jsonify({'success': False, 'msg': 'Artículo no encontrado'})
    
    elif action == 'adjust':
        try:
            item_id = request.form.get('item_id')
            if not item_id:
                return jsonify({'success': False, 'msg': 'ID de artículo no proporcionado'})
            try:
                adjustment = int(request.form.get('adjustment', request.form.get('adjust_qty', 0)))
            except (ValueError, TypeError):
                return jsonify({'success': False, 'msg': 'El ajuste debe ser un número entero'})

            item = Stock.query.get(int(item_id))
            if not item:
                return jsonify({'success': False, 'msg': 'Artículo no encontrado'})

            new_qty = item.quantity + adjustment
            if new_qty < 0:
                return jsonify({'success': False, 'msg': f'Stock insuficiente. Stock actual: {item.quantity}'})

            item.quantity = new_qty
            db.session.commit()
            check_low_stock()
            return jsonify({'success': True, 'msg': 'Stock ajustado', 'new_quantity': item.quantity})
        except SQLAlchemyError as e:
            db.session.rollback()
            return jsonify({'success': False, 'msg': 'Error al ajustar el stock'})

    elif action == 'delete':
        try:
            item_id = request.form.get('item_id')
            if not item_id:
                return jsonify({'success': False, 'msg': 'ID de artículo no proporcionado'})
            item = Stock.query.get(int(item_id))
            if not item:
                return jsonify({'success': False, 'msg': 'Artículo no encontrado'})
            db.session.delete(item)
            db.session.commit()
            return jsonify({'success': True, 'msg': 'Artículo eliminado'})
        except SQLAlchemyError as e:
            db.session.rollback()
            return jsonify({'success': False, 'msg': 'Error al eliminar el artículo'})
    
    return jsonify({'success': False, 'msg': 'Acción no válida'})

@app.route('/manage_stock_categories', methods=['POST'])
@login_required
def manage_stock_categories():
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    try:
        action = request.form.get('action')
        
        if action == 'add':
            name = request.form.get('name', '').strip()
            parent_id = request.form.get('parent_id', '').strip()
            
            if not name:
                return jsonify({'success': False, 'msg': 'El nombre de la categoría es obligatorio'})
            
            parent_id_val = int(parent_id) if parent_id and parent_id.isdigit() else None

            # ✅ CORRECCIÓN: verificar nombre único dentro del mismo nivel jerárquico
            duplicate = StockCategory.query.filter_by(name=name, parent_id=parent_id_val).first()
            if duplicate:
                return jsonify({'success': False, 'msg': f'Ya existe una {"subcategoría" if parent_id_val else "categoría"} con el nombre "{name}"'})
            
            # Validar que el padre exista si se especificó
            if parent_id_val:
                parent = StockCategory.query.get(parent_id_val)
                if not parent:
                    return jsonify({'success': False, 'msg': 'La categoría padre seleccionada no existe'})
                # No permitir subcategorías de subcategorías (máximo 2 niveles)
                if parent.parent_id is not None:
                    return jsonify({'success': False, 'msg': 'No se permiten más de 2 niveles de categorías'})
            
            new_category = StockCategory(
                name=name,
                parent_id=parent_id_val
            )
            db.session.add(new_category)
            db.session.commit()
            
            return jsonify({'success': True, 'msg': f'{"Subcategoría" if parent_id_val else "Categoría"} "{name}" creada correctamente', 'id': new_category.id})
        
        elif action == 'delete':
            category_id = request.form.get('category_id')
            category = StockCategory.query.get(category_id)
            
            if category:
                # Si tiene subcategorías, no permitir eliminar
                if category.subcategories:
                    return jsonify({'success': False, 'msg': 'No se puede eliminar una categoría con subcategorías'})
                
                # Los productos se quedan sin categoría (category_id = None)
                for item in category.items:
                    item.category_id = None
                
                db.session.delete(category)
                db.session.commit()
                
                return jsonify({'success': True, 'msg': 'Categoría eliminada'})
        
        return jsonify({'success': False, 'msg': 'Acción no válida'})
    
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error en manage_stock_categories: {str(e)}")
        return jsonify({'success': False, 'msg': f'Error en la base de datos: {str(e)}'})
    except Exception as e:
        db.session.rollback()
        print(f"Error inesperado en manage_stock_categories: {str(e)}")
        return jsonify({'success': False, 'msg': f'Error: {str(e)}'})

@app.route('/save_report', methods=['POST'])
@login_required
def save_report():
    """Guardar parte de trabajo desde el panel técnico"""
    try:
        linked_task_id = request.form.get('linked_task_id')
        client_name = request.form.get('client_name')
        service_type_name = request.form.get('service_type')
        date_str = request.form.get('date', '')
        task_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None

        # Nuevos timestamps del parte v2
        parte_transport_start = request.form.get('parte_transport_start', '').strip() or None
        parte_arrival         = request.form.get('parte_arrival', '').strip() or None
        parte_work_start      = request.form.get('parte_work_start', '').strip() or None
        parte_work_end        = request.form.get('parte_work_end', '').strip() or None

        # Compatibilidad: entry_time = parte_work_start, exit_time = parte_work_end
        entry_time = parte_work_start or request.form.get('entry_time', '').strip() or None
        exit_time  = parte_work_end   or request.form.get('exit_time', '').strip()  or None

        description = request.form.get('description')
        parts_text = request.form.get('parts_text', '')

        # ✅ VALIDACIÓN: Solo cliente es obligatorio
        if not client_name:
            flash('⚠️ El nombre del cliente es obligatorio', 'danger')
            return redirect(url_for('dashboard'))
        
        # Stock - Ahora soporta múltiples items con diferentes acciones
        stock_item_ids = request.form.getlist('stock_item_id[]')
        stock_qtys = request.form.getlist('stock_quantity[]')
        stock_actions = request.form.getlist('stock_action[]')
        
        # Firma digital (el campo HTML se llama 'signature_name', no 'signature_client_name')
        signature_data = request.form.get('signature_data')
        signature_name = request.form.get('signature_name') or request.form.get('signature_client_name', '')
        
        # Duración del cronómetro (formato HH:MM:SS, enviado desde el campo oculto)
        work_duration = request.form.get('work_duration', '').strip() or None
        
        if not signature_data:
            flash('⚠️ La firma del cliente es obligatoria', 'danger')
            return redirect(url_for('dashboard'))
        
        # Buscar cliente y servicio
        client = Client.query.filter_by(name=client_name).first()
        client_id = client.id if client else None
        
        service_type = ServiceType.query.filter_by(name=service_type_name).first()
        if not service_type:
            flash('Tipo de servicio no válido', 'danger')
            return redirect(url_for('dashboard'))
        
        # ✅ MEJORA: Procesar múltiples items de stock con diferentes acciones
        stock_items_used = []
        for i, item_id in enumerate(stock_item_ids):
            if item_id and item_id != '' and int(item_id) > 0:
                qty = int(stock_qtys[i]) if i < len(stock_qtys) and stock_qtys[i] else 0
                action = stock_actions[i] if i < len(stock_actions) else 'usar'
                
                if qty > 0:
                    stock_item = Stock.query.get(int(item_id))
                    if stock_item:
                        # Actualizar cantidad en stock según la acción
                        if action in ['usar', 'retirar']:
                            if stock_item.quantity >= qty:
                                stock_item.quantity -= qty
                            else:
                                flash(f'⚠️ No hay suficiente stock de {stock_item.name}', 'danger')
                                db.session.rollback()
                                return redirect(url_for('dashboard'))
                        elif action == 'devolver':
                            stock_item.quantity += qty
                        
                        stock_items_used.append({
                            'id': stock_item.id,
                            'name': stock_item.name,
                            'quantity': qty,
                            'action': action
                        })
        
        # Si hay una cita vinculada, actualizar esa tarea
        if linked_task_id and linked_task_id != 'none':
            task = Task.query.get(int(linked_task_id))
            # Permitir completar si: técnico asignado, técnico secundario, admin, o tarea sin asignar
            _is_extra = task and db.session.query(TaskTechnician).filter_by(
                task_id=task.id, user_id=current_user.id).first()
            _can_complete = (
                task and (
                    current_user.role == 'admin'
                    or task.tech_id == current_user.id
                    or bool(_is_extra)
                    or task.tech_id is None
                )
            )
            if _can_complete:
                # Si la tarea estaba sin asignar, registrar al técnico que la completa
                if task.tech_id is None and current_user.role == 'tech':
                    task.tech_id = current_user.id
                    task.status = 'Pendiente'  # normalizar antes de poner Completado
                # Actualizar la tarea existente
                task.description = description
                task.parts_text = parts_text
                task.signature_data = signature_data
                task.signature_client_name = signature_name
                task.signature_timestamp = datetime.now() + timedelta(hours=1)
                task.status = 'Completado'
                task.work_end_time = datetime.now() + timedelta(hours=1)
                if work_duration:
                    task.work_duration = work_duration
                # Guardar hora de entrada y salida
                if entry_time: task.start_time = entry_time
                if exit_time: task.end_time = exit_time
                # Guardar timestamps v2
                if parte_transport_start: task.parte_transport_start = parte_transport_start
                if parte_arrival:         task.parte_arrival         = parte_arrival
                if parte_work_start:      task.parte_work_start      = parte_work_start
                if parte_work_end:        task.parte_work_end        = parte_work_end
                
                # Guardar items de stock
                if stock_items_used:
                    task.stock_item_id = stock_items_used[0]['id']
                    task.stock_quantity_used = stock_items_used[0]['quantity']
                    task.stock_action = stock_items_used[0]['action']
                    if len(stock_items_used) > 1:
                        stock_details = ', '.join([f"{item['name']} ({item['quantity']}) - {item['action']}" for item in stock_items_used])
                        task.parts_text = f"{parts_text}\n[Stock: {stock_details}]" if parts_text else f"[Stock: {stock_details}]"
                
                # ✅ MEJORA: Manejar archivos adjuntos con metadatos completos
                if 'attachments' in request.files:
                    files = request.files.getlist('attachments')
                    attachments_data = []
                    
                    # Cargar attachments existentes si los hay
                    if task.attachments:
                        try:
                            attachments_data = json.loads(task.attachments)
                            if not isinstance(attachments_data, list):
                                attachments_data = []
                        except:
                            attachments_data = []
                    
                    for file in files:
                        if file and file.filename and allowed_file(file.filename):
                            original_filename = secure_filename(file.filename)
                            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                            saved_filename = f"task_{task.id}_{timestamp}_{original_filename}"
                            filepath = os.path.join(app.config['UPLOAD_FOLDER'], saved_filename)
                            file.save(filepath)
                            
                            # Guardar con metadatos
                            attachments_data.append({
                                'filename': saved_filename,
                                'original_name': original_filename,
                                'size': os.path.getsize(filepath)
                            })
                    
                    if attachments_data:
                        task.attachments = json.dumps(attachments_data)
                
                db.session.commit()
                check_low_stock()
                
                flash('✅ Parte vinculado completado y firmado correctamente.', 'success')
                return redirect(url_for('dashboard'))
        
        # Si no hay cita vinculada, crear una nueva tarea completada
        new_task = Task(
            tech_id=current_user.id,
            client_id=client_id,
            client_name=client_name,
            date=task_date,
            start_time=entry_time,
            end_time=exit_time,
            service_type_id=service_type.id if service_type else None,
            description=description,
            parts_text=parts_text,
            signature_data=signature_data,
            signature_client_name=signature_name,
            signature_timestamp=datetime.now() + timedelta(hours=1),
            status='Completado',
            work_end_time=datetime.now() + timedelta(hours=1),
            work_duration=work_duration,
            parte_transport_start=parte_transport_start,
            parte_arrival=parte_arrival,
            parte_work_start=parte_work_start,
            parte_work_end=parte_work_end,
        )
        
        # Guardar items de stock
        if stock_items_used:
            new_task.stock_item_id = stock_items_used[0]['id']
            new_task.stock_quantity_used = stock_items_used[0]['quantity']
            new_task.stock_action = stock_items_used[0]['action']
            if len(stock_items_used) > 1:
                stock_details = ', '.join([f"{item['name']} ({item['quantity']}) - {item['action']}" for item in stock_items_used])
                new_task.parts_text = f"{parts_text}\n[Stock: {stock_details}]" if parts_text else f"[Stock: {stock_details}]"
        
        db.session.add(new_task)
        db.session.commit()
        
        # ✅ MEJORA: Manejar archivos adjuntos con metadatos completos
        if 'attachments' in request.files:
            files = request.files.getlist('attachments')
            attachments_data = []
            
            for file in files:
                if file and file.filename and allowed_file(file.filename):
                    original_filename = secure_filename(file.filename)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    saved_filename = f"task_{new_task.id}_{timestamp}_{original_filename}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], saved_filename)
                    file.save(filepath)
                    
                    # Guardar con metadatos
                    attachments_data.append({
                        'filename': saved_filename,
                        'original_name': original_filename,
                        'size': os.path.getsize(filepath)
                    })
            
            if attachments_data:
                new_task.attachments = json.dumps(attachments_data)
                db.session.commit()
        
        check_low_stock()
        
        flash('✅ Parte de trabajo creado y firmado correctamente.', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Error en save_report: {e}")
        flash(f'Error al guardar el parte: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/upload_task_file/<int:task_id>', methods=['POST'])
@login_required
def upload_task_file(task_id):
    task = Task.query.get_or_404(task_id)
    
    if current_user.role != 'admin' and current_user.id != task.tech_id:
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'msg': 'No se envió ningún archivo'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'msg': 'Nombre de archivo vacío'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"task_{task_id}_{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        attachments = []
        if task.attachments:
            try:
                attachments = json.loads(task.attachments)
            except:
                pass
        
        attachments.append(filename)
        task.attachments = json.dumps(attachments)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'filename': filename,
            'msg': 'Archivo subido correctamente'
        })
    
    return jsonify({'success': False, 'msg': 'Tipo de archivo no permitido'}), 400

@app.route('/api/parte/draft', methods=['GET', 'POST', 'DELETE'])
@login_required
def parte_draft():
    """API para borrador de parte – persiste en fichero JSON por usuario"""
    draft_dir = os.path.join(basedir, 'parte_drafts')
    os.makedirs(draft_dir, exist_ok=True)
    draft_path = os.path.join(draft_dir, f'draft_{current_user.id}.json')

    if request.method == 'GET':
        if os.path.exists(draft_path):
            try:
                with open(draft_path, 'r', encoding='utf-8') as f:
                    return jsonify({'success': True, 'draft': json.load(f)})
            except Exception:
                pass
        return jsonify({'success': True, 'draft': None})

    elif request.method == 'POST':
        try:
            data = request.get_json(force=True) or {}
            data['saved_at'] = datetime.now().isoformat()
            with open(draft_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'msg': str(e)})

    elif request.method == 'DELETE':
        try:
            if os.path.exists(draft_path):
                os.remove(draft_path)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'msg': str(e)})

# --- API ENDPOINTS ---

@app.route('/api/tasks/filter')
@login_required
def filter_tasks():
    """Endpoint para filtrar tareas con múltiples criterios"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    try:
        service_type = request.args.get('service_type', '').strip()
        status = request.args.get('status', '').strip()
        tech_id = request.args.get('tech_id', '').strip()
        client_name = request.args.get('client_name', '').strip()
        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()
        
        query = Task.query
        
        if service_type:
            service = ServiceType.query.filter_by(name=service_type).first()
            if service:
                query = query.filter_by(service_type_id=service.id)
        
        if status:
            query = query.filter_by(status=status)
        
        if tech_id:
            query = query.filter_by(tech_id=int(tech_id))
        
        if client_name:
            query = query.filter(Task.client_name.ilike(f'%{client_name}%'))
        
        if date_from:
            try:
                df = datetime.strptime(date_from, '%Y-%m-%d').date()
                query = query.filter(Task.date >= df)
            except:
                pass
        
        if date_to:
            try:
                dt = datetime.strptime(date_to, '%Y-%m-%d').date()
                query = query.filter(Task.date <= dt)
            except:
                pass
        
        tasks = query.order_by(Task.date.desc()).limit(500).all()
        
        results = []
        for task in tasks:
            results.append({
                'id': task.id,
                'client_name': task.client_name or '—',
                'service_type': task.service_type.name if task.service_type else '—',
                'status': task.status,
                'date': task.date.strftime('%d/%m/%Y') if task.date else '—',
                'time': task.start_time or '—',
                'tech': task.tech.username if task.tech else 'Sin asignar',
                'description': task.description or ''
            })
        
        return jsonify({'success': True, 'data': results, 'total': len(results)})
    
    except Exception as e:
        print(f"Error filtering tasks: {str(e)}")
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/tasks')
@login_required
def get_all_tasks():
    """Obtener todas las tareas para el calendario - CON MANEJO ROBUSTO DE ERRORES"""
    try:
        if current_user.role == 'admin':
            tech_id = request.args.get('tech_id')
            if tech_id:
                try:
                    tasks = Task.query.filter_by(tech_id=int(tech_id)).all()
                except Exception as e:
                    print(f"Error filtrando tareas por técnico: {e}")
                    tasks = []
            else:
                try:
                    tasks = Task.query.all()
                except Exception as e:
                    print(f"Error cargando todas las tareas: {e}")
                    tasks = []
        else:
            # Incluir tareas donde el usuario es técnico principal O secundario
            try:
                primary_tasks = Task.query.filter_by(tech_id=current_user.id).all()
            except Exception as e:
                print(f"Error cargando tareas primarias: {e}")
                primary_tasks = []
            
            try:
                extra_task_ids = db.session.query(TaskTechnician.task_id).filter_by(user_id=current_user.id).all()
                extra_task_ids = [r[0] for r in extra_task_ids]
                extra_tasks = Task.query.filter(Task.id.in_(extra_task_ids), Task.tech_id != current_user.id).all() if extra_task_ids else []
            except Exception as e:
                print(f"Error cargando tareas secundarias: {e}")
                extra_tasks = []
            
            tasks = primary_tasks + extra_tasks
        
        events = []
        for task in tasks:
            try:
                # Obtener tipo de servicio
                service_type = None
                try:
                    if task.service_type_id:
                        service_type = ServiceType.query.get(task.service_type_id)
                except Exception as e:
                    print(f"Error cargando servicio de tarea {task.id}: {e}")
                
                color = service_type.color if service_type else '#6c757d'
                
                # Obtener todos los técnicos de la cita
                extra_techs = []
                try:
                    if hasattr(task, 'extra_technicians'):
                        extra_techs = [tt.user.username for tt in task.extra_technicians if tt.user]
                except Exception as e:
                    print(f"Error cargando técnicos secundarios de tarea {task.id}: {e}")
                
                # Técnico principal
                tech_name = 'Sin asignar'
                try:
                    if task.tech:
                        tech_name = task.tech.username
                except Exception as e:
                    print(f"Error cargando técnico principal de tarea {task.id}: {e}")
                
                all_tech_names = tech_name
                if extra_techs:
                    all_tech_names += ', ' + ', '.join(extra_techs)
                
                # Construir evento
                is_remote_at = bool(getattr(task, 'is_remote', False))
                event = {
                    'id': task.id,
                    'title': ('📡 ' if is_remote_at else '') + f"{task.client_name if task.client_name else 'Sin cliente'} - {service_type.name if service_type else 'Sin tipo'}",
                    'start': f"{task.date}T{task.start_time}:00" if (task.date and task.start_time) else str(task.date) if task.date else '',
                    'end': f"{task.date}T{task.end_time}:00" if (task.date and task.end_time) else str(task.date) if task.date else '',
                    'backgroundColor': '#06b6d4' if is_remote_at else color,
                    'borderColor': '#0891b2' if is_remote_at else color,
                    'extendedProps': {
                        'client': task.client_name or 'Sin cliente',
                        'client_id': task.client_id,
                        'service_type': service_type.name if service_type else 'Sin tipo',
                        'status': task.status or 'Pendiente',
                        'tech_id': task.tech_id,
                        'tech_name': all_tech_names,
                        'desc': task.description or '',
                        'has_signature': bool(task.signature_data),
                        'is_remote': is_remote_at,
                        'remote_hours': getattr(task, 'remote_support_hours', 0) or 0,
                    }
                }
                events.append(event)
                
            except Exception as e:
                print(f"Error procesando tarea {task.id}: {e}")
                continue
        
        return jsonify(events)
        
    except Exception as e:
        print(f"Error CRÍTICO en get_all_tasks: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error cargando tareas: {str(e)}'}), 500

@app.route('/api/stock_search')
@login_required
def stock_search():
    """API para buscador de piezas/stock por nombre"""
    q = request.args.get('q', '').strip()
    if len(q) < 1:
        return jsonify([])
    items = Stock.query.filter(Stock.name.ilike(f'%{q}%')).order_by(Stock.name).limit(15).all()
    return jsonify([{
        'id': item.id,
        'name': item.name,
        'quantity': item.quantity,
        'category': item.category.name if item.category else None
    } for item in items])

@app.route('/api/clients_search')
@login_required
def api_clients_search():
    """API para autocompletado de clientes"""
    query = request.args.get('q', '').strip()
    
    if len(query) < 2:
        return jsonify([])
    
    clients = Client.query.filter(
        Client.name.ilike(f'%{query}%')
    ).order_by(Client.name).limit(10).all()
    
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'phone': c.phone,
        'email': c.email,
        'address': c.address,
        'link': c.link,
        'has_support': c.has_support
    } for c in clients])

@app.route('/api/clients')
@login_required
def get_clients():
    """API para autocompletado de clientes (alias)"""
    return api_clients_search()

@app.route('/api/get_task_full/<int:task_id>')
@login_required
def get_task_full(task_id):
    """
    API para obtener datos completos de una tarea
    Usado cuando se selecciona una cita en el formulario de parte
    """
    task = Task.query.get_or_404(task_id)
    
    # Verificar permisos: admin, técnico asignado, técnico secundario, o tarea sin asignar
    _extra = db.session.query(TaskTechnician).filter_by(task_id=task_id, user_id=current_user.id).first()
    if (current_user.role != 'admin'
            and current_user.id != task.tech_id
            and not _extra
            and task.tech_id is not None):
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    service_type = ServiceType.query.get(task.service_type_id) if task.service_type_id else None
    
    # ✅ INCLUIR INFORMACIÓN COMPLETA DEL CLIENTE
    client_info = None
    if task.client:
        client_info = {
            'name': task.client.name,
            'phone': task.client.phone,
            'email': task.client.email,
            'address': task.client.address,
            'link': task.client.link,
            'notes': task.client.notes,
            'has_support': task.client.has_support
        }
    
    return jsonify({
        'success': True,
        'data': {
            'id': task.id,
            'client_name': task.client_name,
            'client_info': client_info,  # ✅ AÑADIDO
            'date': task.date.strftime('%Y-%m-%d') if task.date else None,
            'start_time': task.start_time or '',
            'end_time': task.end_time or '',
            'service_type': service_type.name if service_type else '',
            'description': task.description or ''
        }
    })

@app.route('/api/task/<int:task_id>')
@login_required
def get_task_details(task_id):
    task = Task.query.get_or_404(task_id)
    
    if current_user.role != 'admin' and current_user.id != task.tech_id:
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    attachments_list = []
    if task.attachments:
        try:
            attachments_list = json.loads(task.attachments)
        except:
            pass
    
    service_type = ServiceType.query.get(task.service_type_id) if task.service_type_id else None
    
    # ✅ INCLUIR INFORMACIÓN COMPLETA DEL CLIENTE
    client_info = None
    if task.client:
        client_info = {
            'name': task.client.name,
            'phone': task.client.phone,
            'email': task.client.email,
            'address': task.client.address,
            'link': task.client.link,
            'notes': task.client.notes,
            'has_support': task.client.has_support
        }
    
    return jsonify({
        'success': True,
        'data': {
            'id': task.id,
            'client_name': task.client_name,
            'client_info': client_info,  # ✅ AÑADIDO
            'date': task.date.strftime('%Y-%m-%d') if task.date else None,
            'start_time': task.start_time,
            'end_time': task.end_time,
            'service_type': service_type.name if service_type else 'Sin tipo',
            'service_type_id': task.service_type_id,
            'description': task.description,
            'parts_text': task.parts_text,
            'status': task.status,
            'tech_name': task.tech.username if task.tech else 'SIN TÉCNICO',
            'tech_id': task.tech_id,
            'attachments': attachments_list,
            'has_signature': bool(task.signature_data),
            'signature_client_name': task.signature_client_name,
            'signature_timestamp': task.signature_timestamp.strftime('%d/%m/%Y %H:%M') if task.signature_timestamp else None,
            'work_start_time': task.work_start_time.strftime('%H:%M') if task.work_start_time else None,
            'work_end_time': task.work_end_time.strftime('%H:%M') if task.work_end_time else None,
            'work_duration': task.work_duration or None,
            'stock_info': {
                'item_name': task.stock_item.name if task.stock_item else None,
                'quantity': task.stock_quantity_used,
                'action': task.stock_action
            } if task.stock_item else None
        }
    })

# ====== NUEVA RUTA: TECH_ANALYTICS (CORREGIDA - SIN INGRESOS NI TOP CLIENTES) ======
@app.route('/api/tech_analytics')
@login_required
def get_tech_analytics():
    """Estadísticas del técnico actual (para panel técnico)"""
    period = request.args.get('period', '30')
    
    # Calcular fecha de inicio según período
    if period == 'all':
        start_date = date(2020, 1, 1)  # Fecha arbitraria en el pasado
    else:
        days = int(period)
        start_date = date.today() - timedelta(days=days)
    
    # Obtener tareas del técnico en el período
    tasks = Task.query.filter(
        Task.tech_id == current_user.id,
        Task.status == 'Completado',
        Task.date >= start_date
    ).all()
    
    # Calcular estadísticas
    total_services = len(tasks)
    total_maintenances = sum(1 for t in tasks if t.service_type and 'manten' in t.service_type.name.lower())

    total_minutes = 0
    time_count = 0
    for task in tasks:
        mins = _task_duration_minutes(task)
        if mins > 0:
            total_minutes += mins
            time_count += 1

    total_h, total_m = total_minutes // 60, total_minutes % 60
    total_hours_str = f"{total_h}h {total_m:02d}min" if total_m else f"{total_h}h"
    avg_time = round((total_minutes / time_count) / 60, 1) if time_count > 0 else 0
    
    # Distribución por tipo de servicio
    service_distribution = {}
    for task in tasks:
        service_name = task.service_type.name if task.service_type else 'Sin tipo'
        service_distribution[service_name] = service_distribution.get(service_name, 0) + 1
    
    # Timeline de los últimos meses
    timeline_data = []
    for i in range(5, -1, -1):
        month_date = date.today() - timedelta(days=i*30)
        month_start = month_date.replace(day=1)
        if i > 0:
            next_month = month_date + timedelta(days=30)
            month_end = next_month.replace(day=1)
        else:
            month_end = date.today()
        
        month_tasks = Task.query.filter(
            Task.tech_id == current_user.id,
            Task.date >= month_start,
            Task.date < month_end,
            Task.status == 'Completado'
        ).count()
        
        timeline_data.append({
            'month': month_start.strftime('%b'),
            'services': month_tasks,
            'maintenances': month_tasks // 3  # Estimación
        })
    
    return jsonify({
        'total_services': total_services,
        'total_maintenances': total_maintenances,
        'total_hours': total_hours_str,
        'avg_time': avg_time,
        'service_distribution': service_distribution,
        'timeline_data': timeline_data
    })

@app.route('/api/tech_stats/<int:tech_id>')
@login_required
def get_tech_stats(tech_id):
    """Análisis individual de trabajador con lista detallada de servicios"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    tech = User.query.get_or_404(tech_id)
    tasks = Task.query.filter_by(tech_id=tech_id, status='Completado').all()
    
    service_stats = {}
    total_minutes = 0

    for task in tasks:
        service_type = ServiceType.query.get(task.service_type_id) if task.service_type_id else None
        service_name = service_type.name if service_type else 'Sin tipo'

        if service_name not in service_stats:
            service_stats[service_name] = {'count': 0, 'tasks': []}

        service_stats[service_name]['count'] += 1

        task_mins = _task_duration_minutes(task)
        total_minutes += task_mins

        if task_mins > 0:
            dur_h, dur_m = task_mins // 60, task_mins % 60
            dur_str = f"{dur_h}h {dur_m:02d}min" if dur_h else f"{dur_m}min"
        else:
            dur_str = '—'

        service_stats[service_name]['tasks'].append({
            'id': task.id,
            'client': task.client_name,
            'date': task.date.strftime('%d/%m/%Y') if task.date else None,
            'time': f"{task.start_time} - {task.end_time}" if task.start_time and task.end_time else 'No especificado',
            'duration': dur_str,
            'description': task.description or 'Sin descripción',
            'has_attachments': bool(task.attachments),
            'has_signature': bool(task.signature_data)
        })

    total_h, total_m = total_minutes // 60, total_minutes % 60
    total_hours_str = f"{total_h}h {total_m:02d}min" if total_m else f"{total_h}h"

    return jsonify({
        'success': True,
        'data': {
            'tech_name': tech.username,
            'total_completed': len(tasks),
            'total_hours': total_hours_str,
            'total_hours_raw': round(total_minutes / 60, 2),
            'service_breakdown': service_stats
        }
    })

@app.route('/api/admin_analytics')
@login_required
def get_admin_analytics():
    """Estadísticas globales para el administrador"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    tech_id = request.args.get('tech_id', type=int)
    period = request.args.get('period', 'all')
    date_from_str = request.args.get('from')
    date_to_str = request.args.get('to')
    
    query = Task.query
    
    if tech_id:
        query = query.filter_by(tech_id=tech_id)
    
    # Filtro de período
    if period == 'week':
        week_ago = date.today() - timedelta(days=7)
        query = query.filter(Task.date >= week_ago)
    elif period == 'month':
        month_ago = date.today() - timedelta(days=30)
        query = query.filter(Task.date >= month_ago)
    elif period == 'custom':
        # Filtro personalizado por fecha
        try:
            if date_from_str:
                date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
                query = query.filter(Task.date >= date_from)
            if date_to_str:
                date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
                query = query.filter(Task.date <= date_to)
        except Exception as e:
            print(f"Error parsing custom dates: {e}")
    
    all_tasks = query.all()
    completed_tasks = query.filter_by(status='Completado').all()
    pending_tasks = query.filter_by(status='Pendiente').all()
    
    task_types = {}
    total_for_percentage = len(completed_tasks) if completed_tasks else 1
    
    for task in completed_tasks:
        service_type = ServiceType.query.get(task.service_type_id) if task.service_type_id else None
        service_name = service_type.name if service_type else 'Sin tipo'
        service_color = service_type.color if service_type else '#6c757d'
        
        if service_name not in task_types:
            task_types[service_name] = {
                'count': 0,
                'color': service_color,
                'percentage': 0
            }
        
        task_types[service_name]['count'] += 1
    
    for service_name in task_types:
        count = task_types[service_name]['count']
        task_types[service_name]['percentage'] = round((count / total_for_percentage) * 100, 1)
    
    monthly_tasks = []
    for i in range(5, -1, -1):
        month_date = date.today() - timedelta(days=i*30)
        month_start = month_date.replace(day=1)
        if i > 0:
            next_month = month_date + timedelta(days=30)
            month_end = next_month.replace(day=1)
        else:
            month_end = date.today()
        
        month_tasks = Task.query.filter(
            Task.date >= month_start,
            Task.date < month_end,
            Task.status == 'Completado'
        )
        if tech_id:
            month_tasks = month_tasks.filter_by(tech_id=tech_id)
        
        monthly_tasks.append({
            'month': month_start.strftime('%b'),
            'count': month_tasks.count()
        })
    
    active_techs = User.query.filter_by(role='tech').count()
    
    return jsonify({
        'success': True,
        'data': {
            'total_tasks': len(all_tasks),
            'completed_tasks': len(completed_tasks),
            'pending_tasks': len(pending_tasks),
            'active_technicians': active_techs,
            'task_types': task_types,
            'monthly_tasks': monthly_tasks
        }
    })

@app.route('/api/tech_profile/<int:user_id>', methods=['GET'])
@login_required
def get_tech_profile(user_id):
    """Obtener perfil extendido de un técnico"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    tech = User.query.filter_by(id=user_id, role='tech').first()
    if not tech:
        return jsonify({'success': False, 'msg': 'Técnico no encontrado'}), 404
    profile = tech.profile  # puede ser None si nunca se editó
    return jsonify({
        'success': True,
        'user': {'id': tech.id, 'username': tech.username, 'email': tech.email},
        'profile': {
            'full_name': profile.full_name if profile else '',
            'phone': profile.phone if profile else '',
            'address': profile.address if profile else '',
            'emergency_contact': profile.emergency_contact if profile else '',
            'emergency_phone': profile.emergency_phone if profile else '',
            'start_date': profile.start_date if profile else '',
            'dni': profile.dni if profile else '',
            'internal_notes': profile.internal_notes if profile else '',
            'updated_at': profile.updated_at.strftime('%d/%m/%Y %H:%M') if profile and profile.updated_at else ''
        }
    })

@app.route('/api/tech_profile/<int:user_id>', methods=['POST'])
@login_required
def save_tech_profile(user_id):
    """Guardar/actualizar perfil extendido de un técnico"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    try:
        tech = User.query.filter_by(id=user_id, role='tech').first()
        if not tech:
            return jsonify({'success': False, 'msg': 'Técnico no encontrado'}), 404
        profile = tech.profile
        if not profile:
            profile = TechProfile(user_id=user_id)
            db.session.add(profile)
        profile.full_name = request.form.get('full_name', '').strip()
        profile.phone = request.form.get('phone', '').strip()
        profile.address = request.form.get('address', '').strip()
        profile.emergency_contact = request.form.get('emergency_contact', '').strip()
        profile.emergency_phone = request.form.get('emergency_phone', '').strip()
        profile.start_date = request.form.get('start_date', '').strip()
        profile.dni = request.form.get('dni', '').strip()
        profile.internal_notes = request.form.get('internal_notes', '').strip()
        profile.updated_at = datetime.now()
        db.session.commit()
        return jsonify({'success': True, 'msg': 'Perfil actualizado correctamente'})
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error guardando perfil técnico: {e}")
        return jsonify({'success': False, 'msg': 'Error al guardar el perfil'})

@app.route('/api/stock_categories')
@login_required
def get_stock_categories():
    """Obtener categorías de stock en formato jerárquico"""
    def build_tree(parent_id=None):
        categories = StockCategory.query.filter_by(parent_id=parent_id).all()
        result = []
        for cat in categories:
            result.append({
                'id': cat.id,
                'name': cat.name,
                'children': build_tree(cat.id),
                'items': [{'id': item.id, 'name': item.name, 'quantity': item.quantity, 'min_stock': item.min_stock, 'supplier': item.supplier or 'N/A'} 
                         for item in cat.items]
            })
        return result
    
    return jsonify(build_tree())

# ✅ NUEVA RUTA: Obtener info de un item de stock para editar
@app.route('/api/stock_item/<int:item_id>')
@login_required
def get_stock_item(item_id):
    """Obtener datos de un artículo de stock"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    item = Stock.query.get_or_404(item_id)
    
    return jsonify({
        'success': True,
        'data': {
            'id': item.id,
            'name': item.name,
            'quantity': item.quantity,
            'min_stock': item.min_stock,
            'supplier': item.supplier or '',
            'category_id': item.category_id,
            'description': item.description or ''
        }
    })

# --- RUTAS DE ALARMAS ---
@app.route('/api/alarms')
@login_required
def get_alarms():
    if current_user.role != 'admin':
        return jsonify([])
    
    alarms = Alarm.query.order_by(Alarm.is_read.asc(), Alarm.created_at.desc()).all()
    return jsonify([{
        'id': a.id,
        'type': a.alarm_type,
        'title': a.title,
        'description': a.description,
        'client_name': a.client_name,
        'created_at': a.created_at.strftime('%d/%m/%Y %H:%M'),
        'is_read': a.is_read,
        'priority': a.priority
    } for a in alarms])

@app.route('/mark_alarm_read/<int:alarm_id>', methods=['POST'])
@login_required
def mark_alarm_read(alarm_id):
    if current_user.role != 'admin':
        return jsonify({'success': False}), 403
    
    alarm = Alarm.query.get_or_404(alarm_id)
    alarm.is_read = True
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/create_alarm', methods=['POST'])
@login_required
def create_alarm():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    
    alarm_type = request.form.get('alarm_type')
    title = request.form.get('title')
    description = request.form.get('description')
    client_name = request.form.get('client_name', None)
    priority = request.form.get('priority', 'normal')
    
    new_alarm = Alarm(
        alarm_type=alarm_type,
        title=title,
        description=description,
        client_name=client_name,
        priority=priority
    )
    db.session.add(new_alarm)
    db.session.commit()
    
    flash('Alarma creada correctamente.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/api/report_detail/<int:task_id>')
@login_required
def api_report_detail(task_id):
    """Detalle completo de un informe/parte para el modal de admin"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    try:
        t = Task.query.get_or_404(task_id)
        svc_name = t.service_type.name if t.service_type else ('Asistencia Remota' if t.is_remote else '—')
        tech_name = t.tech.username if t.tech else 'Sin técnico'
        date_str = t.date.strftime('%d/%m/%Y') if t.date else '—'

        # Calcular tiempo de transporte
        transport_duration = ''
        if t.parte_transport_start and t.parte_arrival:
            try:
                from datetime import datetime as _dt2
                t0 = _dt2.strptime(t.parte_transport_start, '%H:%M')
                t1 = _dt2.strptime(t.parte_arrival, '%H:%M')
                diff_min = int((t1 - t0).total_seconds() / 60)
                if diff_min > 0:
                    h, m = divmod(diff_min, 60)
                    transport_duration = f'{h}h {m:02d}min' if h else f'{m}min'
            except Exception:
                pass

        # Adjuntos
        attachments = []
        if t.attachments:
            try:
                atts = json.loads(t.attachments)
                if isinstance(atts, list):
                    for a in atts:
                        if isinstance(a, dict):
                            attachments.append({'name': a.get('original_name', a.get('filename', '')), 'filename': a.get('filename', '')})
                        else:
                            attachments.append({'name': str(a), 'filename': str(a)})
            except Exception:
                pass

        return jsonify({
            'success':               True,
            'id':                    t.id,
            'is_remote':             bool(t.is_remote),
            'client_name':           t.client_name or '—',
            'service_type':          svc_name,
            'date':                  date_str,
            'tech':                  tech_name,
            # Tiempos del parte presencial
            'parte_transport_start': t.parte_transport_start or '',
            'parte_arrival':         t.parte_arrival or '',
            'parte_work_start':      t.parte_work_start or '',
            'parte_work_end':        t.parte_work_end or '',
            'transport_duration':    transport_duration,
            'work_duration':         t.work_duration or '',
            # Tiempos asistencia remota
            'start_time':            t.start_time or '',
            'end_time':              t.end_time or '',
            'remote_support_hours':  t.remote_support_hours or 0,
            # Contenido
            'description':           t.description or '',
            'parts_text':            t.parts_text or '',
            # Firma
            'has_signature':         bool(t.signature_data),
            'signature_client_name': t.signature_client_name or '',
            'signature_timestamp':   t.signature_timestamp.strftime('%d/%m/%Y %H:%M') if t.signature_timestamp else '',
            # Adjuntos
            'attachments':           attachments,
        })
    except Exception as e:
        print(f"Error en api_report_detail: {e}")
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/reports')
@login_required
def api_reports():
    """API endpoint para obtener informes completados con filtros, incluyendo asistencias remotas"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    try:
        client_filter  = request.args.get('client', '').strip().lower()
        date_from_str  = request.args.get('date_from', '')
        date_to_str    = request.args.get('date_to', '')

        query = Task.query.filter_by(status='Completado')

        if client_filter:
            query = query.filter(Task.client_name.ilike(f'%{client_filter}%'))
        if date_from_str:
            try:
                df = datetime.strptime(date_from_str, '%Y-%m-%d').date()
                query = query.filter(Task.date >= df)
            except Exception:
                pass
        if date_to_str:
            try:
                dt = datetime.strptime(date_to_str, '%Y-%m-%d').date()
                query = query.filter(Task.date <= dt)
            except Exception:
                pass

        tasks = query.order_by(Task.date.desc()).limit(200).all()

        results = []
        for t in tasks:
            svc_name = t.service_type.name if t.service_type else ('Asistencia Remota' if t.is_remote else '—')
            tech_name = t.tech.username if t.tech else 'Sin técnico'
            date_str = t.date.strftime('%d/%m/%Y') if t.date else '—'

            # Adjuntos
            has_attachments = False
            att_count = 0
            if t.attachments:
                try:
                    atts = json.loads(t.attachments)
                    if isinstance(atts, list) and len(atts) > 0:
                        has_attachments = True
                        att_count = len(atts)
                except Exception:
                    pass

            # Calcular tiempo de transporte si hay datos
            transport_duration = ''
            if t.parte_transport_start and t.parte_arrival:
                try:
                    from datetime import datetime as _dt2
                    t0 = _dt2.strptime(t.parte_transport_start, '%H:%M')
                    t1 = _dt2.strptime(t.parte_arrival, '%H:%M')
                    diff_min = int((t1 - t0).total_seconds() / 60)
                    if diff_min > 0:
                        h, m = divmod(diff_min, 60)
                        transport_duration = f'{h}h {m:02d}min' if h else f'{m}min'
                except Exception:
                    pass

            results.append({
                'id':                    t.id,
                'client_name':           t.client_name or '—',
                'service_type':          svc_name,
                'date':                  date_str,
                'tech':                  tech_name,
                'work_duration':         t.work_duration or '',
                'has_attachments':       has_attachments,
                'attachments_count':     att_count,
                'is_remote':             bool(t.is_remote),
                # Timestamps del parte
                'parte_transport_start': t.parte_transport_start or '',
                'parte_arrival':         t.parte_arrival or '',
                'parte_work_start':      t.parte_work_start or '',
                'parte_work_end':        t.parte_work_end or '',
                'transport_duration':    transport_duration,
                # Datos adicionales para el detalle
                'start_time':            t.start_time or '',
                'end_time':              t.end_time or '',
                'description':           t.description or '',
                'parts_text':            t.parts_text or '',
                'remote_support_hours':  t.remote_support_hours or 0,
            })

        return jsonify({'success': True, 'data': results, 'total': len(results)})
    except Exception as e:
        print(f"Error en api_reports: {e}")
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/print_report/<int:report_id>')
@login_required
def print_report(report_id):
    """Endpoint para imprimir/exportar reporte de trabajo"""
    try:
        task = Task.query.get_or_404(report_id)
        
        # Verificar permisos
        if current_user.role != 'admin' and task.tech_id != current_user.id:
            flash('No tienes permiso para ver este reporte', 'danger')
            return redirect(url_for('dashboard'))
        
        # Parsear archivos adjuntos si existen
        attachments_data = []
        if task.attachments:
            try:
                filenames = json.loads(task.attachments)
                for filename in filenames:
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    if os.path.exists(filepath):
                        file_size = os.path.getsize(filepath)
                        attachments_data.append({
                            'filename': filename,
                            'original_name': filename.split('_', 3)[-1] if '_' in filename else filename,
                            'size': file_size
                        })
            except Exception as e:
                print(f"Error parsing attachments: {e}")
                attachments_data = []
        
        return render_template('print_report.html', 
                             task=task,
                             attachments=attachments_data,
                             now=datetime.now)
    except Exception as e:
        print(f"Error printing report {report_id}: {str(e)}")
        flash('Error al cargar el reporte', 'danger')
        return redirect(url_for('dashboard'))


@app.route('/complete_task/<int:task_id>', methods=['POST'])
@login_required
def complete_task(task_id):
    """Completar una tarea desde el panel técnico vía JSON (firma, stock, descripción)"""
    try:
        task = Task.query.get_or_404(task_id)
        _is_extra = db.session.query(TaskTechnician).filter_by(task_id=task_id, user_id=current_user.id).first()
        _is_unassigned = (task.tech_id is None)
        _allowed = (current_user.role == 'admin' or task.tech_id == current_user.id or bool(_is_extra) or _is_unassigned)
        if not _allowed:
            return jsonify({'success': False, 'msg': 'No autorizado'}), 403

        data = request.get_json() or {}
        description     = data.get('description', task.description or '')
        parts           = data.get('parts', task.parts_text or '')
        signature       = data.get('signature')
        sig_client_name = data.get('signature_client_name', '')
        stock_item_id   = data.get('stock_item_id')
        stock_quantity  = int(data.get('stock_quantity', 0) or 0)
        stock_action_val= data.get('stock_action', 'usar')

        if not signature:
            return jsonify({'success': False, 'msg': 'La firma del cliente es obligatoria'}), 400

        task.description           = description
        task.parts_text            = parts
        task.signature_data        = signature
        task.signature_client_name = sig_client_name
        task.signature_timestamp   = datetime.now() + timedelta(hours=1)
        task.status                = 'Completado'
        task.work_end_time         = datetime.now() + timedelta(hours=1)

        if _is_unassigned and current_user.role == 'tech':
            task.tech_id = current_user.id

        if stock_item_id and stock_quantity > 0:
            stock_item = Stock.query.get(int(stock_item_id))
            if stock_item:
                if stock_action_val in ('usar', 'retirar'):
                    if stock_item.quantity >= stock_quantity:
                        stock_item.quantity -= stock_quantity
                    else:
                        return jsonify({'success': False, 'msg': f'Stock insuficiente de {stock_item.name}'}), 400
                elif stock_action_val == 'devolver':
                    stock_item.quantity += stock_quantity
                task.stock_item_id       = stock_item.id
                task.stock_quantity_used = stock_quantity
                task.stock_action        = stock_action_val

        db.session.commit()
        return jsonify({'success': True, 'msg': 'Parte completado correctamente'})

    except Exception as e:
        db.session.rollback()
        print(f"Error en complete_task {task_id}: {e}")
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/api/task_action/<int:task_id>/<action>', methods=['POST'])
@login_required
def task_action(task_id, action):
    """Endpoint para acciones sobre tareas (completar, eliminar, cancelar, toggle)"""
    task = Task.query.get_or_404(task_id)
    
    # Verificar permisos:
    # - Admin: acceso total
    # - Técnico asignado o secundario: acceso a sus tareas
    # - Técnico con tarea sin asignar (tech_id=None): puede ver, completar y hacer toggle
    # - Ningún otro rol puede actuar
    _is_extra_tech = (current_user.role == 'tech' and
                      db.session.query(TaskTechnician).filter_by(
                          task_id=task_id, user_id=current_user.id).first() is not None)
    _is_unassigned = (task.tech_id is None)
    _tech_allowed = (current_user.role == 'tech' and
                     (task.tech_id == current_user.id or _is_extra_tech or _is_unassigned))

    if current_user.role != 'admin' and not _tech_allowed:
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403

    try:
        if action == 'complete':
            # Si la tarea estaba sin asignar, asignarla al técnico que la completa
            if _is_unassigned and current_user.role == 'tech':
                task.tech_id = current_user.id
            task.status = 'Completado'
            if not task.work_end_time:
                task.work_end_time = datetime.now(timezone(timedelta(hours=1)))
            db.session.commit()
            return jsonify({'success': True, 'msg': 'Tarea completada', 'status': task.status})

        elif action == 'toggle':
            # Toggle entre Completado y Pendiente/Sin asignar
            if task.status == 'Completado':
                # Descompletar: volver al estado anterior según si tiene técnico
                task.status = 'Pendiente' if task.tech_id else 'Sin asignar'
                task.work_end_time = None
            else:
                # Completar: si sin asignar, registrar técnico
                if _is_unassigned and current_user.role == 'tech':
                    task.tech_id = current_user.id
                task.status = 'Completado'
                if not task.work_end_time:
                    task.work_end_time = datetime.now(timezone(timedelta(hours=1)))
            db.session.commit()
            return jsonify({'success': True, 'msg': f'Tarea marcada como {task.status}', 'status': task.status})
        
        elif action == 'delete':
            db.session.delete(task)
            db.session.commit()
            return jsonify({'success': True, 'msg': 'Tarea eliminada'})
        
        elif action == 'cancel':
            task.status = 'Cancelado'
            db.session.commit()
            return jsonify({'success': True, 'msg': 'Tarea cancelada', 'status': task.status})
        
        else:
            return jsonify({'success': False, 'msg': 'Acción no válida'}), 400
    
    except Exception as e:
        print(f"Error in task_action: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'msg': 'Error al procesar la acción'}), 500

@app.route('/api/get_task/<int:task_id>')
@login_required
def get_task(task_id):
    """Obtener datos de una tarea específica"""
    task = Task.query.get_or_404(task_id)
    
    if current_user.role != 'admin' and task.tech_id != current_user.id:
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    service_type = ServiceType.query.get(task.service_type_id) if task.service_type_id else None
    
    return jsonify({
        'success': True,
        'data': {
            'id': task.id,
            'client_name': task.client_name,
            'date': task.date.strftime('%Y-%m-%d') if task.date else None,
            'time': task.start_time or '',
            'service_type': service_type.name if service_type else '',
            'notes': task.description or '',
            'tech_id': task.tech_id
        }
    })

@app.route('/api/task_details/<int:task_id>')
@login_required
def api_task_details(task_id):
    """Obtener detalles completos de una tarea"""
    task = Task.query.get_or_404(task_id)
    
    if current_user.role != 'admin' and task.tech_id != current_user.id:
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    service_type = ServiceType.query.get(task.service_type_id) if task.service_type_id else None
    
    # Parsear attachments
    attachments_list = []
    if task.attachments:
        try:
            attachments_list = json.loads(task.attachments)
        except:
            pass
    
    return jsonify({
        'success': True,
        'data': {
            'id': task.id,
            'client_name': task.client_name,
            'tech_name': task.tech.username if task.tech else 'Sin asignar',
            'date': task.date.strftime('%Y-%m-%d') if task.date else None,
            'start_time': task.start_time or '',
            'end_time': task.end_time or '',
            'service_type': service_type.name if service_type else 'Sin tipo',
            'status': task.status,
            'description': task.description or '',
            'parts_text': task.parts_text or '',
            'has_signature': bool(task.signature_data),
            'attachments': attachments_list,
            'stock_info': {
                'item_name': task.stock_item.name if task.stock_item else None,
                'quantity': task.stock_quantity_used,
                'action': task.stock_action
            } if task.stock_item else None
        }
    })



# --- RUTAS DE PAGOS ---
@app.route('/api/payments/client/<int:client_id>', methods=['GET'])
@login_required
def get_client_payment(client_id):
    """Obtener datos de pago de un cliente"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    client = Client.query.get(client_id)
    if not client:
        return jsonify({'success': False, 'msg': 'Cliente no encontrado'}), 404
    payment = ClientPayment.query.filter_by(client_id=client_id).first()
    if not payment:
        return jsonify({'success': True, 'data': {
            'id': None, 'client_id': client_id, 'client_name': client.name,
            'total_amount': 0, 'budget_number': '', 'first_payment': 0,
            'records': [], 'total_paid': 0, 'pending': 0
        }})
    records = [{'id': r.id, 'amount': r.amount, 'date': r.date.strftime('%Y-%m-%d'),
                'notes': r.notes or '', 'is_paid': bool(getattr(r, 'is_paid', False))}
               for r in sorted(payment.records, key=lambda r: r.date)]
    total_paid = payment.first_payment + sum(r.amount for r in payment.records)
    pending = max(0, payment.total_amount - total_paid)
    return jsonify({'success': True, 'data': {
        'id': payment.id, 'client_id': client_id, 'client_name': client.name,
        'total_amount': payment.total_amount, 'budget_number': payment.budget_number or '',
        'first_payment': payment.first_payment, 'records': records,
        'total_paid': round(total_paid, 2), 'pending': round(pending, 2)
    }})


@app.route('/api/payments/client/<int:client_id>', methods=['POST'])
@login_required
def save_client_payment(client_id):
    """Guardar datos de pago principales"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    client = Client.query.get(client_id)
    if not client:
        return jsonify({'success': False, 'msg': 'Cliente no encontrado'}), 404
    try:
        data = request.get_json()
        payment = ClientPayment.query.filter_by(client_id=client_id).first()
        if not payment:
            payment = ClientPayment(client_id=client_id)
            db.session.add(payment)
        payment.total_amount = float(data.get('total_amount', 0))
        payment.budget_number = data.get('budget_number', '').strip()
        payment.first_payment = float(data.get('first_payment', 0))
        payment.updated_at = datetime.now()
        db.session.commit()
        return jsonify({'success': True, 'payment_id': payment.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/payments/record', methods=['POST'])
@login_required
def add_payment_record():
    """Anadir cobro parcial"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    try:
        data = request.get_json()
        client_id = int(data.get('client_id'))
        amount = float(data.get('amount', 0))
        date_str = data.get('date', date.today().strftime('%Y-%m-%d'))
        notes = data.get('notes', '').strip()
        payment = ClientPayment.query.filter_by(client_id=client_id).first()
        if not payment:
            client = Client.query.get(client_id)
            if not client:
                return jsonify({'success': False, 'msg': 'Cliente no encontrado'}), 404
            payment = ClientPayment(client_id=client_id)
            db.session.add(payment)
            db.session.flush()
        record_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        is_paid = bool(data.get('is_paid', False))
        record = PaymentRecord(client_payment_id=payment.id, amount=amount, date=record_date, notes=notes, is_paid=is_paid)
        db.session.add(record)
        db.session.commit()
        return jsonify({'success': True, 'record_id': record.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/payments/record/<int:record_id>', methods=['DELETE'])
@login_required
def delete_payment_record(record_id):
    """Eliminar cobro parcial"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    try:
        record = PaymentRecord.query.get(record_id)
        if not record:
            return jsonify({'success': False, 'msg': 'Registro no encontrado'}), 404
        db.session.delete(record)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/payments/record/<int:record_id>/toggle_paid', methods=['POST'])
@login_required
def toggle_payment_record_paid(record_id):
    """Alternar estado pagado/pendiente de un cobro parcial"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    try:
        record = PaymentRecord.query.get(record_id)
        if not record:
            return jsonify({'success': False, 'msg': 'Registro no encontrado'}), 404
        record.is_paid = not bool(getattr(record, 'is_paid', False))
        db.session.commit()
        return jsonify({'success': True, 'is_paid': bool(record.is_paid)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/payments/summary')
@login_required
def payments_summary():
    """Resumen de pagos de todos los clientes (mismo listado que CLIENTES, siempre actualizado)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    try:
        clients = Client.query.order_by(Client.name).all()
        result = []
        for client in clients:
            payment = client.payment
            if payment:
                total_paid = payment.first_payment + sum(r.amount for r in payment.records)
                pending = max(0, payment.total_amount - total_paid)
                records = list(payment.records)  # materializar para evitar lazy-load repetido
                if not records:
                    # Sin cobros parciales: pendiente si hay importe total, neutro si no
                    status = 'pending' if payment.total_amount > 0 else 'none'
                elif all(getattr(r, 'is_paid', False) for r in records):
                    status = 'paid'
                else:
                    status = 'pending'
            else:
                total_paid = 0
                pending = 0
                status = 'none'
            result.append({
                'id': client.id,
                'name': client.name,
                'phone': client.phone,
                'has_support': client.has_support,
                'total_amount': payment.total_amount if payment else 0,
                'budget_number': payment.budget_number if payment else '',
                'total_paid': round(total_paid, 2),
                'pending': round(pending, 2),
                'status': status
            })
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"Error en payments_summary: {e}")
        return jsonify({'success': False, 'msg': f'Error interno: {str(e)}'}), 500

@app.route('/api/admin/tech_colors')
@login_required
def get_tech_colors():
    """Endpoint para obtener colores asignados a cada técnico en el calendario global"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    TECH_COLORS = [
        '#3b82f6', '#22c55e', '#a855f7', '#f59e0b', '#ef4444',
        '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#14b8a6',
    ]
    
    techs = User.query.filter_by(role='tech').order_by(User.id).all()
    result = []
    for i, tech in enumerate(techs):
        result.append({
            'id': tech.id,
            'username': tech.username,
            'color': TECH_COLORS[i % len(TECH_COLORS)]
        })
    
    return jsonify({'success': True, 'data': result})

@app.route('/api/admin/all_tasks')
@login_required
def admin_all_tasks():
    """Endpoint para calendario global del admin - retorna eventos en formato FullCalendar"""
    try:
        if current_user.role != 'admin':
            return jsonify([])
        
        try:
            tasks = Task.query.all()
        except Exception as e:
            print(f"Error cargando tareas: {e}")
            return jsonify([]), 500
        
        events = []
        
        # Paleta de colores para diferenciar técnicos
        TECH_COLORS = [
            '#3b82f6',  # azul
            '#22c55e',  # verde
            '#a855f7',  # morado
            '#f59e0b',  # ámbar
            '#ef4444',  # rojo
            '#06b6d4',  # cian
            '#ec4899',  # rosa
            '#84cc16',  # lima
            '#f97316',  # naranja
            '#14b8a6',  # teal
        ]

        def get_contrast_color(hex_color):
            """Devuelve #000 o #fff para máximo contraste"""
            try:
                h = hex_color.lstrip('#')
                if len(h) < 6:
                    return '#ffffff'
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
                return '#000000' if luminance > 0.5 else '#ffffff'
            except Exception:
                return '#ffffff'
        
        # Obtener todos los técnicos y asignarles colores
        try:
            techs = User.query.filter_by(role='tech').order_by(User.id).all()
            tech_color_map = {}
            for i, tech in enumerate(techs):
                tech_color_map[tech.id] = TECH_COLORS[i % len(TECH_COLORS)]
        except Exception as e:
            print(f"Error cargando técnicos: {e}")
            tech_color_map = {}
        
        # Procesar cada tarea
        for task in tasks:
            try:
                # ✅ Excluir tareas sin técnico del calendario (solo aparecen en lista inferior)
                if task.tech_id is None:
                    continue

                # Obtener tipo de servicio
                service_type = None
                try:
                    if task.service_type_id:
                        service_type = ServiceType.query.get(task.service_type_id)
                except Exception as e:
                    print(f"Error cargando servicio para tarea {task.id}: {e}")
                
                # Color del técnico
                tech_color = tech_color_map.get(task.tech_id, '#6c757d')
                text_color = get_contrast_color(tech_color)
                
                # Obtener nombre del técnico
                tech_name = ''
                try:
                    if task.tech:
                        tech_name = task.tech.username
                except Exception as e:
                    print(f"Error cargando técnico para tarea {task.id}: {e}")
                
                # Construir datetime para el evento
                event_start = None
                if task.date and task.start_time:
                    try:
                        # Combinar fecha y hora
                        dt_str = f"{task.date.isoformat()}T{task.start_time}:00"
                        event_start = dt_str
                    except Exception as e:
                        print(f"Error construyendo fecha para tarea {task.id}: {e}")
                
                # Si no hay fecha, usar fecha actual (para tareas sin asignar)
                if not event_start:
                    event_start = f"{date.today().isoformat()}T00:00:00"
                
                # Construir evento
                is_remote = bool(getattr(task, 'is_remote', False))
                event = {
                    'id': str(task.id),
                    'title': ('📡 ' if is_remote else '') + (task.client_name if task.client_name else 'Sin cliente'),
                    'start': event_start,
                    'backgroundColor': '#06b6d4' if is_remote else tech_color,
                    'borderColor': '#0891b2' if is_remote else tech_color,
                    'textColor': text_color,
                    'extendedProps': {
                        'status': task.status,
                        'client': task.client_name or 'Sin cliente',
                        'client_id': task.client_id,
                        'tech_id': task.tech_id,
                        'tech_name': tech_name or 'Sin asignar',
                        'tech_color': tech_color,
                        'text_color': text_color,
                        'service_type': service_type.name if service_type else 'Sin tipo',
                        'desc': task.description or '',
                        'has_attachments': bool(task.attachments),
                        'is_remote': is_remote,
                        'remote_hours': getattr(task, 'remote_support_hours', 0) or 0,
                    }
                }
                
                events.append(event)
                
            except Exception as e:
                print(f"Error procesando tarea {task.id}: {e}")
                continue
        
        return jsonify(events)
        
    except Exception as e:
        print(f"Error en admin_all_tasks: {e}")
        return jsonify([]), 500

@app.route('/api/admin/unassigned_tasks')
@login_required
def get_unassigned_tasks():
    """Obtener tareas sin técnico asignado"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    try:
        tasks = Task.query.filter(
            Task.tech_id == None,
            Task.status == 'Sin asignar'
        ).order_by(Task.id.desc()).all()
        
        result = []
        for task in tasks:
            result.append({
                'id': task.id,
                'client_name': task.client_name,
                'description': task.description,
                'service_type_id': task.service_type_id,
                'service_type_name': task.service_type.name if task.service_type else '',
                'status': task.status
            })
        
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"Error getting unassigned tasks: {str(e)}")
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/api/tech/unassigned_tasks')
@login_required
def get_tech_unassigned_tasks():
    """Obtener tareas sin técnico asignado - accesible para técnicos"""
    try:
        tasks = Task.query.filter(
            Task.tech_id == None,
            Task.status == 'Sin asignar'
        ).order_by(Task.date.asc()).all()

        result = []
        for task in tasks:
            service_type = ServiceType.query.get(task.service_type_id) if task.service_type_id else None
            result.append({
                'id': task.id,
                'client_name': task.client_name,
                'description': task.description,
                'date': task.date.strftime('%d/%m/%Y') if task.date else '—',
                'service_type_name': service_type.name if service_type else '—',
                'service_type_color': service_type.color if service_type else '#6c757d',
            })

        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"Error getting unassigned tasks for tech: {str(e)}")
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/api/task/<int:task_id>/assign_tech', methods=['POST'])
@login_required
def assign_tech_to_task(task_id):
    """Asignar técnico a una tarea sin técnico - MEJORADO"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    try:
        task = Task.query.get(task_id)
        if not task:
            return jsonify({'success': False, 'msg': 'Tarea no encontrada'}), 404
        
        data = request.get_json() or {}
        tech_id = data.get('tech_id')
        date_str = data.get('date')
        start_time = data.get('start_time')
        end_time = data.get('end_time', '')
        
        # Validaciones mejoradas
        if not tech_id:
            return jsonify({'success': False, 'msg': 'Técnico es obligatorio'}), 400
        if not date_str:
            return jsonify({'success': False, 'msg': 'Fecha es obligatoria'}), 400
        if not start_time:
            return jsonify({'success': False, 'msg': 'Hora de inicio es obligatoria'}), 400
        
        tech = User.query.get(tech_id)
        if not tech or tech.role != 'tech':
            return jsonify({'success': False, 'msg': 'Técnico no válido'}), 404
        
        try:
            task_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'msg': 'Formato de fecha inválido (use YYYY-MM-DD)'}), 400
        
        task.tech_id = tech_id
        task.date = task_date
        task.start_time = start_time
        task.end_time = end_time if end_time else None
        task.status = 'Pendiente'
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'msg': f'Tarea asignada a {tech.username} correctamente',
            'task_id': task.id,
            'task': {
                'id': task.id,
                'date': task.date.isoformat(),
                'start_time': task.start_time,
                'end_time': task.end_time,
                'tech_id': tech_id,
                'status': 'Pendiente',
                'client_name': task.client_name
            }
        })
    except Exception as e:
        db.session.rollback()
        print(f"Error assigning tech to task: {str(e)}")
        return jsonify({'success': False, 'msg': f'Error: {str(e)}'}), 500

@app.route('/api/task/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    """Eliminar una tarea (admin o técnico asignado)"""
    try:
        task = Task.query.get(task_id)
        if not task:
            return jsonify({'success': False, 'msg': 'Tarea no encontrada'}), 404
        
        # Verificar permisos: admin o técnico asignado a la tarea
        if current_user.role != 'admin' and task.tech_id != current_user.id:
            return jsonify({'success': False, 'msg': 'No tienes permiso para eliminar esta tarea'}), 403
        
        db.session.delete(task)
        db.session.commit()
        
        return jsonify({'success': True, 'msg': 'Tarea eliminada'})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting task: {str(e)}")
        return jsonify({'success': False, 'msg': f'Error: {str(e)}'}), 500

# ✅ NUEVO: Endpoints para cronómetros persistentes
@app.route('/api/timer/save', methods=['POST'])
@login_required
def save_timer():
    """Guardar estado del cronómetro en servidor"""
    try:
        data = request.get_json()
        timer_type = data.get('timer_type')  # 'work', 'travel', 'remote'
        elapsed = data.get('elapsed_seconds', 0)
        task_id = data.get('task_id')
        is_active = data.get('is_active', True)
        
        # Buscar sesión activa
        existing = TimerSession.query.filter_by(
            user_id=current_user.id,
            timer_type=timer_type,
            is_active=True
        ).first()
        
        timer_id = None
        if existing:
            existing.elapsed_seconds = elapsed
            existing.is_active = is_active
            timer_id = existing.id
        else:
            timer = TimerSession(
                user_id=current_user.id,
                timer_type=timer_type,
                elapsed_seconds=elapsed,
                task_id=task_id,
                is_active=is_active
            )
            db.session.add(timer)
            db.session.flush()
            timer_id = timer.id
        
        db.session.commit()
        return jsonify({'success': True, 'timer_id': timer_id})
    except Exception as e:
        db.session.rollback()
        print(f"Error saving timer: {str(e)}")
        return jsonify({'success': False}), 500

@app.route('/api/timer/restore', methods=['GET'])
@login_required
def restore_timer():
    """Restaurar estado del cronómetro al abrir página"""
    try:
        timer_type = request.args.get('type')  # 'work', 'travel', 'remote'
        
        # Buscar sesión activa
        timer = TimerSession.query.filter_by(
            user_id=current_user.id,
            timer_type=timer_type,
            is_active=True
        ).first()
        
        if timer:
            return jsonify({
                'success': True,
                'elapsed_seconds': timer.elapsed_seconds,
                'task_id': timer.task_id,
                'timer_id': timer.id
            })
        return jsonify({'success': False})
    except Exception as e:
        print(f"Error restoring timer: {str(e)}")
        return jsonify({'success': False}), 500

@app.route('/api/timer/<int:timer_id>/stop', methods=['POST'])
@login_required
def stop_timer(timer_id):
    """Detener un cronómetro y registrarlo"""
    try:
        data = request.get_json()
        final_seconds = data.get('elapsed_seconds', 0)
        
        timer = TimerSession.query.get(timer_id)
        if not timer or timer.user_id != current_user.id:
            return jsonify({'success': False, 'msg': 'No encontrado'}), 404
        
        timer.is_active = False
        timer.ended_at = datetime.now()
        timer.elapsed_seconds = final_seconds
        
        # Si es tarea, actualizar duración
        if timer.task_id:
            task = Task.query.get(timer.task_id)
            if task:
                hours = final_seconds / 3600
                if timer.timer_type == 'remote':
                    task.remote_support_hours = hours
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        print(f"Error stopping timer: {str(e)}")
        return jsonify({'success': False}), 500

# ✅ NUEVO: Endpoint para asistencia remota
@app.route('/api/remote_assistance', methods=['POST'])
@login_required
def create_remote_assistance():
    """Crear asistencia remota con soporte opcional de fecha/hora/técnico"""
    if current_user.role not in ('admin', 'tech'):
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    try:
        data = request.get_json()
        client_name = data.get('client_name')
        client_id   = data.get('client_id')
        description = data.get('description', '')
        date_val    = data.get('date')        # YYYY-MM-DD  (opcional)
        start_time  = data.get('start_time')  # HH:MM       (opcional)
        end_time    = data.get('end_time')    # HH:MM       (opcional)
        tech_id     = data.get('tech_id')     # int         (opcional)
        
        if not client_name:
            return jsonify({'success': False, 'msg': 'Cliente requerido'}), 400

        # Los técnicos siempre se asignan a sí mismos (ignoran tech_id del payload)
        if current_user.role == 'tech':
            tech_id = current_user.id

        # Detectar tipo de servicio "Asistencia Remota" o crear como primer servicio disponible
        remote_service = ServiceType.query.filter(
            ServiceType.name.ilike('%remot%')
        ).first() or ServiceType.query.first()

        # Si hay técnico + fecha → Pendiente; si no → Sin asignar
        status = 'Pendiente' if (tech_id and date_val) else 'Sin asignar'

        task = Task(
            tech_id=int(tech_id) if tech_id else None,
            client_id=int(client_id) if client_id else None,
            client_name=client_name,
            description=description,
            is_remote=True,
            status=status,
            created_by=current_user.id,
            service_type_id=remote_service.id if remote_service else None,
        )

        if date_val:
            from datetime import date as _date
            task.date = date_val
        if start_time:
            task.start_time = start_time
        if end_time:
            task.end_time = end_time

        db.session.add(task)
        db.session.commit()
        
        return jsonify({'success': True, 'task_id': task.id, 'status': status})
    except Exception as e:
        db.session.rollback()
        print(f"Error creating remote assistance: {str(e)}")
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/remote_task/<int:task_id>/update', methods=['POST'])
@login_required
def update_remote_task(task_id):
    """Actualizar una asistencia remota (hora inicio/fin, descripción, completar)"""
    try:
        task = Task.query.get(task_id)
        if not task:
            return jsonify({'success': False, 'msg': 'Tarea no encontrada'}), 404
        if not task.is_remote:
            return jsonify({'success': False, 'msg': 'No es una asistencia remota'}), 400

        # Permitir al admin O al técnico asignado
        if current_user.role != 'admin' and current_user.id != task.tech_id:
            return jsonify({'success': False, 'msg': 'No autorizado'}), 403

        data = request.get_json()
        start_time   = data.get('start_time')
        end_time     = data.get('end_time')
        description  = data.get('description')
        mark_complete = data.get('mark_complete', False)

        # Guardar horas
        if start_time:
            task.start_time = start_time
        if end_time:
            task.end_time = end_time
        if description is not None:
            task.description = description

        # Calcular duración si hay inicio y fin
        duration_hours = 0.0
        if task.start_time and task.end_time:
            try:
                from datetime import datetime as _dt
                t0 = _dt.strptime(task.start_time, '%H:%M')
                t1 = _dt.strptime(task.end_time, '%H:%M')
                diff = (t1 - t0).total_seconds() / 3600.0
                if diff > 0:
                    duration_hours = round(diff, 2)
                    task.remote_support_hours = duration_hours
                    # Generar work_duration formateado
                    h = int(diff)
                    m = int(round((diff - h) * 60))
                    task.work_duration = f"{h}h {m:02d}min" if h else f"{m}min"
            except Exception as _e:
                print(f"Error calculando duración: {_e}")

        # Límite mensual de horas de soporte por cliente
        MONTHLY_LIMIT = 5.0
        warning_msg = None
        if task.client_id and duration_hours > 0:
            from datetime import date as _date
            now = _date.today()
            past_tasks = Task.query.filter(
                Task.client_id == task.client_id,
                Task.is_remote == True,
                Task.status == 'Completado',
                Task.id != task_id,
                Task.date != None,
                db.func.extract('year',  Task.date) == now.year,
                db.func.extract('month', Task.date) == now.month,
            ).all()
            used_hours = sum(t.remote_support_hours or 0 for t in past_tasks)
            new_total  = used_hours + duration_hours
            if new_total > MONTHLY_LIMIT:
                warning_msg = (
                    f"⚠️ El cliente acumulará {new_total:.2f}h de soporte remoto este mes "
                    f"(límite: {MONTHLY_LIMIT}h). Se ha guardado igualmente."
                )

        if mark_complete:
            task.status = 'Completado'
            task.work_end_time = datetime.now(timezone(timedelta(hours=1)))
            # Asignar fecha de hoy si la tarea no tiene fecha aún
            if not task.date:
                task.date = date.today()

        db.session.commit()
        return jsonify({
            'success': True,
            'duration_hours': duration_hours,
            'work_duration': task.work_duration,
            'warning': warning_msg,
            'status': task.status
        })
    except Exception as e:
        db.session.rollback()
        print(f"Error updating remote task: {str(e)}")
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/client/<int:client_id>/monthly_remote_hours', methods=['GET'])
@login_required
def get_client_monthly_remote_hours(client_id):
    """Horas de soporte remoto del cliente en el mes actual"""
    try:
        from datetime import date as _date
        import calendar as _cal
        client = Client.query.get(client_id)
        if not client:
            return jsonify({'success': False}), 404

        now = _date.today()
        tasks = Task.query.filter(
            Task.client_id == client_id,
            Task.is_remote == True,
            Task.status == 'Completado',
            Task.date != None,
            db.func.extract('year',  Task.date) == now.year,
            db.func.extract('month', Task.date) == now.month,
        ).all()

        MONTHLY_LIMIT = 5.0
        used_hours  = sum(t.remote_support_hours or 0 for t in tasks)
        remaining   = max(0.0, MONTHLY_LIMIT - used_hours)
        month_name  = _cal.month_name[now.month]

        return jsonify({
            'success': True,
            'used_hours':      round(used_hours, 2),
            'remaining_hours': round(remaining, 2),
            'limit_hours':     MONTHLY_LIMIT,
            'month':           month_name,
            'session_count':   len(tasks),
            'over_limit':      used_hours >= MONTHLY_LIMIT,
        })
    except Exception as e:
        print(f"Error monthly_remote_hours: {e}")
        return jsonify({'success': False}), 500

@app.route('/edit_stock_item/<int:item_id>', methods=['POST'])
@login_required
def edit_stock_item(item_id):
    """Editar un artículo de stock"""
    if current_user.role != 'admin':
        flash('No tienes permiso para realizar esta acción', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        item = Stock.query.get(item_id)
        if not item:
            return jsonify({'success': False, 'msg': 'Artículo no encontrado'}), 404
        
        item.name = request.form.get('name', item.name)
        item.quantity = int(request.form.get('quantity', item.quantity))
        item.min_stock = int(request.form.get('min_stock', item.min_stock))
        item.description = request.form.get('description', item.description)
        item.supplier = request.form.get('supplier', item.supplier)
        
        db.session.commit()
        return jsonify({'success': True, 'msg': 'Artículo actualizado correctamente'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/import_clients', methods=['POST'])
@login_required
def import_clients():
    """Importar clientes desde CSV"""
    if current_user.role != 'admin':
        flash('No tienes permiso para realizar esta acción', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'msg': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'msg': 'No file selected'}), 400
        
        if not file.filename.endswith(('.csv', '.txt')):
            return jsonify({'success': False, 'msg': 'Solo se aceptan archivos CSV'}), 400
        
        import csv
        import io
        
        stream = io.StringIO(file.stream.read().decode('UTF-8'), newline=None)
        csv_data = csv.DictReader(stream)
        
        count = 0
        errors = []
        
        for row in csv_data:
            try:
                # Validar campos requeridos
                if not row.get('name') or not row.get('phone') or not row.get('email') or not row.get('address'):
                    errors.append(f"Fila {count+1}: Faltan campos requeridos")
                    continue
                
                # Evitar duplicados
                existing = Client.query.filter_by(name=row['name']).first()
                if existing:
                    errors.append(f"Fila {count+1}: Cliente '{row['name']}' ya existe")
                    continue
                
                client = Client(
                    name=row['name'],
                    phone=row['phone'],
                    email=row['email'],
                    address=row['address'],
                    link=row.get('link', ''),
                    notes=row.get('notes', ''),
                    has_support=row.get('has_support', 'False').lower() == 'true',
                    support_schedule=row.get('support_schedule', None)
                )
                db.session.add(client)
                count += 1
            except Exception as e:
                errors.append(f"Fila {count+1}: {str(e)}")
        
        db.session.commit()
        return jsonify({'success': True, 'msg': f'{count} clientes importados', 'errors': errors})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/client/<int:client_id>/support_info', methods=['GET'])
@login_required
def get_client_support_info(client_id):
    """Obtener información de soporte de un cliente"""
    try:
        client = Client.query.get(client_id)
        if not client:
            return jsonify({'success': False}), 404
        
        return jsonify({
            'success': True,
            'data': {
                'id': client.id,
                'name': client.name,
                'phone': client.phone,
                'email': client.email,
                'has_support': client.has_support,
                'support_schedule': client.support_schedule or '',
                'address': client.address
            }
        })
    except Exception as e:
        print(f"Error getting client support: {str(e)}")
        return jsonify({'success': False}), 500


@app.route('/api/admin/tasks/<int:tech_id>')
@login_required
def admin_tech_tasks(tech_id):
    """Endpoint para calendario individual de un técnico desde admin"""
    if current_user.role != 'admin':
        return jsonify([])
    
    tasks = Task.query.filter_by(tech_id=tech_id).all()
    events = []
    
    for task in tasks:
        service_type = ServiceType.query.get(task.service_type_id) if task.service_type_id else None
        color = service_type.color if service_type else '#6c757d'
        
        is_remote_tt = bool(getattr(task, 'is_remote', False))
        events.append({
            'id': task.id,
            'title': ('📡 ' if is_remote_tt else '') + (task.client_name or 'Sin cliente'),
            'start': f"{task.date}T{task.start_time}:00" if task.start_time else str(task.date),
            'end': f"{task.date}T{task.end_time}:00" if task.end_time else str(task.date),
            'backgroundColor': '#06b6d4' if is_remote_tt else color,
            'borderColor': '#0891b2' if is_remote_tt else color,
            'extendedProps': {
                'client': task.client_name or 'Sin cliente',
                'client_id': task.client_id,
                'service_type': service_type.name if service_type else 'Sin tipo',
                'status': task.status,
                'desc': task.description or '',
                'is_remote': is_remote_tt,
                'remote_hours': getattr(task, 'remote_support_hours', 0) or 0,
            }
        })
    
    return jsonify(events)

@app.route('/api/tech/my_tasks')
@login_required
def get_tech_tasks():
    """
    API para obtener tareas del técnico actual (incluyendo las que es técnico secundario).
    Usado por el panel técnico para sincronizar su calendario - CON MANEJO ROBUSTO DE ERRORES
    """
    try:
        if current_user.role != 'tech':
            return jsonify([]), 403
        
        # Tareas donde es técnico principal
        try:
            primary_tasks = Task.query.filter_by(tech_id=current_user.id).all()
        except Exception as e:
            print(f"Error cargando tareas primarias del técnico {current_user.id}: {e}")
            primary_tasks = []
        
        # Tareas donde es técnico secundario
        try:
            secondary_task_ids = db.session.query(TaskTechnician.task_id).filter_by(user_id=current_user.id).all()
            secondary_task_ids = [r[0] for r in secondary_task_ids]
            secondary_tasks = Task.query.filter(Task.id.in_(secondary_task_ids)).all() if secondary_task_ids else []
        except Exception as e:
            print(f"Error cargando tareas secundarias del técnico {current_user.id}: {e}")
            secondary_tasks = []
        
        # Combinar (sin duplicados)
        task_ids = set([t.id for t in primary_tasks] + [t.id for t in secondary_tasks])

        # Incluir tareas sin técnico asignado para que cualquier técnico las vea
        try:
            unassigned_ids = [t.id for t in Task.query.filter(
                Task.tech_id == None, Task.status == 'Sin asignar').all()]
            task_ids.update(unassigned_ids)
        except Exception as e:
            print(f"Error cargando tareas sin asignar: {e}")

        try:
            all_tasks = Task.query.filter(Task.id.in_(task_ids)).all() if task_ids else []
        except Exception as e:
            print(f"Error cargando tareas combinadas del técnico {current_user.id}: {e}")
            all_tasks = []
        
        events = []
        for task in all_tasks:
            try:
                service_type = None
                try:
                    if task.service_type_id:
                        service_type = ServiceType.query.get(task.service_type_id)
                except Exception as e:
                    print(f"Error cargando servicio de tarea {task.id}: {e}")
                
                color = service_type.color if service_type else '#6c757d'
                
                is_remote_gt = bool(getattr(task, 'is_remote', False))
                event = {
                    'id': task.id,
                    'title': ('📡 ' if is_remote_gt else '') + (task.client_name if task.client_name else 'Sin cliente'),
                    'start': f"{task.date}T{task.start_time}:00" if (task.date and task.start_time) else str(task.date) if task.date else '',
                    'end': f"{task.date}T{task.end_time}:00" if (task.date and task.end_time) else str(task.date) if task.date else '',
                    'backgroundColor': '#06b6d4' if is_remote_gt else color,
                    'borderColor': '#0891b2' if is_remote_gt else color,
                    'extendedProps': {
                        'client': task.client_name or 'Sin cliente',
                        'client_id': task.client_id,
                        'service_type': service_type.name if service_type else 'Sin tipo',
                        'status': task.status or 'Pendiente',
                        'desc': task.description or '',
                        'is_remote': is_remote_gt,
                        'remote_hours': getattr(task, 'remote_support_hours', 0) or 0,
                    }
                }
                events.append(event)
                
            except Exception as e:
                print(f"Error procesando tarea {task.id} para técnico {current_user.id}: {e}")
                continue
        
        return jsonify(events)
        
    except Exception as e:
        print(f"Error CRÍTICO en get_tech_tasks para técnico {current_user.id}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error cargando tus tareas: {str(e)}'}), 500

@app.route('/create_appointment', methods=['POST'])
@login_required
def create_appointment():
    """Endpoint para crear una nueva cita desde el panel técnico"""
    try:
        # Obtener datos del request (puede ser JSON o form)
        if request.is_json:
            data = request.json
            client_name = data.get('client_name')
            date_str = data.get('date')
            start_time = data.get('start_time')
            end_time = data.get('end_time', '')  # Opcional
            service_type_id = data.get('service_type_id')
            description = data.get('description', '')  # Opcional
        else:
            client_name = request.form.get('client_name')
            date_str = request.form.get('date')
            start_time = request.form.get('start_time')
            end_time = request.form.get('end_time', '')  # Opcional
            service_type_id = request.form.get('service_type_id')
            description = request.form.get('description', '')  # Opcional
        
        # Validación: SOLO estos 4 campos son obligatorios
        if not client_name or not date_str or not start_time or not service_type_id:
            return jsonify({
                'success': False, 
                'msg': 'Faltan campos obligatorios: Cliente, Fecha, Hora de inicio y Tipo de servicio son requeridos'
            }), 400
        
        # Buscar o crear cliente
        client = Client.query.filter_by(name=client_name).first()
        client_id = client.id if client else None
        
        # Convertir fecha
        task_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # ✅ MEJORADO: Validación de duplicados con logging
        # Solo verifica si existe EXACTAMENTE la misma cita (mismo cliente, fecha, hora)
        print(f"🔍 Validando duplicados para: {client_name} | {task_date} | {start_time} | Tech: {current_user.id}")
        
        existing_task = Task.query.filter_by(
            tech_id=current_user.id,
            client_name=client_name,
            date=task_date,
            start_time=start_time,
            status='Pendiente'
        ).first()
        
        if existing_task:
            print(f"⚠️ Duplicado encontrado: Task ID {existing_task.id}")
            return jsonify({
                'success': False,
                'msg': f'Ya existe una cita pendiente para {client_name} el {task_date.strftime("%d/%m/%Y")} a las {start_time}'
            }), 400
        
        print(f"✅ No hay duplicados, creando cita...")
        
        # Crear tarea
        new_task = Task(
            tech_id=current_user.id,
            client_id=client_id,
            client_name=client_name,
            description=description if description else '',
            date=task_date,
            start_time=start_time,
            end_time=end_time if end_time else None,
            service_type_id=int(service_type_id),
            status='Pendiente'
        )
        
        db.session.add(new_task)
        db.session.commit()
        
        print(f"✅ Cita creada exitosamente: ID {new_task.id} | {client_name} | {task_date} | {start_time}")
        
        return jsonify({
            'success': True,
            'msg': 'Cita creada correctamente',
            'task_id': new_task.id
        })
        
    except Exception as e:
        print(f"Error creating appointment: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'msg': f'Error al crear la cita: {str(e)}'}), 500

@app.route('/create_task_unassigned', methods=['POST'])
@login_required
def create_task_unassigned():
    """Crear tarea sin técnico asignado (Solo admin)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'msg': 'No autorizado'}), 403
    
    try:
        data = request.get_json()
        client_name = data.get('client_name', '').strip()
        service_type_id = data.get('service_type_id')
        description = data.get('description', '').strip()
        
        if not client_name or not service_type_id:
            return jsonify({'success': False, 'msg': 'Cliente y Tipo de servicio son obligatorios'}), 400
        
        # Buscar cliente
        client = Client.query.filter_by(name=client_name).first()
        client_id = client.id if client else None
        
        # Crear tarea sin técnico (sin fecha ni hora)
        new_task = Task(
            tech_id=None,
            client_id=client_id,
            client_name=client_name,
            description=description,
            date=None,
            start_time=None,
            end_time=None,
            service_type_id=int(service_type_id),
            status='Sin asignar',
            created_by=current_user.id
        )
        
        db.session.add(new_task)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'msg': 'Tarea sin asignar creada correctamente',
            'task_id': new_task.id
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error creating unassigned task: {str(e)}")
        return jsonify({'success': False, 'msg': f'Error: {str(e)}'}), 500

@app.route('/edit_appointment/<int:task_id>', methods=['POST'])
@login_required
def edit_appointment(task_id):
    """Endpoint para editar una cita existente"""
    try:
        task = Task.query.get_or_404(task_id)
        
        # Verificar permisos
        if current_user.role != 'admin' and task.tech_id != current_user.id:
            flash('No autorizado', 'danger')
            return redirect(url_for('dashboard'))
        
        # Actualizar datos
        task.client_name = request.form.get('client_name')
        task.date = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date()
        task.start_time = request.form.get('time')
        task.description = request.form.get('notes', '')
        
        # Actualizar técnico si se recibe
        tech_id_str = request.form.get('tech_id', '').strip()
        if tech_id_str:
            tech_id_val = int(tech_id_str)
            tech = User.query.get(tech_id_val)
            if tech and tech.role == 'tech':
                task.tech_id = tech_id_val
                if task.status == 'Sin asignar':
                    task.status = 'Pendiente'
        elif tech_id_str == '':
            # Dejado en blanco => sin asignar (solo si admin)
            if current_user.role == 'admin':
                task.tech_id = None
                task.status = 'Sin asignar'
        
        # Actualizar tipo de servicio
        service_type_name = request.form.get('service_type')
        service_type = ServiceType.query.filter_by(name=service_type_name).first()
        if service_type:
            task.service_type_id = service_type.id
        
        db.session.commit()
        flash('Cita actualizada correctamente', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Error editing appointment: {str(e)}")
        db.session.rollback()
        flash('Error al editar la cita', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/schedule_appointment', methods=['POST'])
@login_required
def schedule_appointment():
    """Endpoint para agendar nueva cita desde el panel admin
    
    CAMBIOS:
    - Crea 1 sola Task (con el primer técnico asignado)
    - Otros técnicos se añaden vía TaskTechnician como técnicos secundarios
    - Registra al admin en created_by
    - Retorna JSON para sincronización automática de calendarios
    """
    try:
        if current_user.role != 'admin':
            return jsonify({'success': False, 'msg': 'Solo administradores pueden agendar citas'}), 403
        
        # Soportar múltiples técnicos (select múltiple envía tech_ids[])
        tech_ids = request.form.getlist('tech_ids[]')
        # Compatibilidad hacia atrás con campo único tech_id
        if not tech_ids:
            single = request.form.get('tech_id')
            if single:
                tech_ids = [single]
        
        client_name = request.form.get('client_name')
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        end_time_str = request.form.get('end_time', '')
        service_type_name = request.form.get('service_type')
        notes = request.form.get('notes', '')
        
        # Validaciones — técnico es OPCIONAL; sin técnico la tarea queda "Sin asignar"
        if not all([client_name, date_str, time_str, service_type_name]):
            return jsonify({'success': False, 'msg': 'Faltan campos obligatorios: cliente, fecha, hora y tipo de servicio'}), 400
        
        # Buscar cliente
        client = Client.query.filter_by(name=client_name).first()
        client_id = client.id if client else None
        
        # Buscar tipo de servicio
        service_type = ServiceType.query.filter_by(name=service_type_name).first()
        if not service_type:
            return jsonify({'success': False, 'msg': 'Tipo de servicio no válido'}), 400
        
        # Convertir fecha
        task_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Técnico principal — puede ser None si el admin no seleccionó ninguno
        primary_tech_id = int(tech_ids[0]) if tech_ids else None
        task_status = 'Pendiente' if primary_tech_id else 'Sin asignar'

        new_task = Task(
            tech_id=primary_tech_id,
            client_id=client_id,
            client_name=client_name,
            description=notes,
            date=task_date,
            start_time=time_str,
            end_time=end_time_str if end_time_str else None,
            service_type_id=service_type.id,
            status=task_status,
            created_by=current_user.id  # Registrar quién creó la tarea
        )
        
        db.session.add(new_task)
        db.session.flush()  # Obtener ID de la nueva tarea
        
        # ✅ NUEVO: Añadir técnicos adicionales como técnicos secundarios
        # (No crear tareas adicionales, solo registrar la relación)
        for i in range(1, len(tech_ids)):
            extra_tech_id = int(tech_ids[i])
            task_tech = TaskTechnician(
                task_id=new_task.id,
                user_id=extra_tech_id
            )
            db.session.add(task_tech)
        
        db.session.commit()
        
        if primary_tech_id:
            num_techs = len(tech_ids)
            msg = f'Cita agendada para {num_techs} técnico{"s" if num_techs > 1 else ""} correctamente'
            print(f"✅ Cita agendada: Task ID {new_task.id} | Tech: {primary_tech_id} | Creada por: {current_user.username}")
        else:
            msg = 'Cita creada sin técnico asignado (estado: Sin asignar)'
            print(f"✅ Cita sin asignar: Task ID {new_task.id} | Creada por: {current_user.username}")

        return jsonify({
            'success': True,
            'task_id': new_task.id,
            'msg': msg
        })
        
    except Exception as e:
        print(f"Error scheduling appointment: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'msg': f'Error al agendar la cita: {str(e)}'}), 500

@app.route('/edit_stock_category/<int:category_id>', methods=['POST'])
@login_required
def edit_stock_category(category_id):
    """Endpoint para editar una categoría de stock"""
    try:
        if current_user.role != 'admin':
            return jsonify({'success': False, 'msg': 'No autorizado'}), 403
        
        category = StockCategory.query.get_or_404(category_id)
        
        name = request.form.get('name')
        parent_id = request.form.get('parent_id')
        
        if name:
            # Verificar que no exista otra categoría con el mismo nombre
            existing = StockCategory.query.filter(
                StockCategory.name == name,
                StockCategory.id != category_id
            ).first()
            
            if existing:
                flash('Ya existe una categoría con ese nombre', 'danger')
                return redirect(url_for('dashboard'))
            
            category.name = name
        
        if parent_id is not None:
            if parent_id == '':
                category.parent_id = None
            else:
                new_parent_id = int(parent_id)
                # Evitar ciclos: no puede ser padre de sí misma
                if new_parent_id == category_id:
                    flash('Una categoría no puede ser padre de sí misma', 'danger')
                    return redirect(url_for('dashboard'))
                # Evitar que una subcategoría se convierta en padre de su padre
                if category.parent_id == new_parent_id:
                    pass  # Sin cambios
                else:
                    category.parent_id = new_parent_id
        
        db.session.commit()
        flash('Categoría actualizada correctamente', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Error editing category: {str(e)}")
        db.session.rollback()
        flash('Error al actualizar la categoría', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/api/stock_category/<int:category_id>')
@login_required
def api_get_stock_category(category_id):
    """API para obtener detalles de una categoría de stock"""
    try:
        category = StockCategory.query.get_or_404(category_id)
        return jsonify({
            'success': True,
            'data': {
                'id': category.id,
                'name': category.name,
                'parent_id': category.parent_id or ''
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/api/task/<int:task_id>/attachments')
@login_required
def api_get_task_attachments(task_id):
    """API para obtener archivos adjuntos de una tarea"""
    try:
        task = Task.query.get_or_404(task_id)
        
        attachments_list = []
        if task.attachments:
            try:
                attachments_data = json.loads(task.attachments)
                if isinstance(attachments_data, list):
                    # Verificar si es formato nuevo (con metadatos) o antiguo (solo nombres)
                    for item in attachments_data:
                        if isinstance(item, dict):
                            # Formato nuevo: ya tiene metadata
                            # Asegurar que tiene todos los campos necesarios
                            attachments_list.append({
                                'filename': item.get('filename', ''),
                                'original_name': item.get('original_name', item.get('filename', 'archivo')),
                                'size': item.get('size', 0)
                            })
                        else:
                            # Formato antiguo: solo nombre de archivo (string)
                            filename = str(item)
                            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                            
                            # Extraer nombre original del filename
                            # Formato: task_X_TIMESTAMP_originalname.ext
                            parts = filename.split('_', 3)
                            original_name = parts[-1] if len(parts) >= 4 else filename
                            
                            # Obtener tamaño del archivo si existe
                            file_size = 0
                            if os.path.exists(filepath):
                                try:
                                    file_size = os.path.getsize(filepath)
                                except:
                                    file_size = 0
                            
                            attachments_list.append({
                                'filename': filename,
                                'original_name': original_name,
                                'size': file_size
                            })
            except json.JSONDecodeError as e:
                print(f"Error parsing attachments JSON: {e}")
                attachments_list = []
            except Exception as e:
                print(f"Error processing attachments: {e}")
                attachments_list = []
        
        return jsonify({
            'success': True,
            'attachments': attachments_list
        })
    except Exception as e:
        print(f"Error in api_get_task_attachments: {e}")
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/api/client/<int:client_id>')
@login_required
def api_get_client(client_id):
    """API para obtener información de un cliente"""
    try:
        client = Client.query.get_or_404(client_id)
        
        return jsonify({
            'success': True,
            'client': {
                'id': client.id,
                'name': client.name,
                'phone': client.phone,
                'email': client.email,
                'address': client.address,
                'link': client.link or '',
                'notes': client.notes or '',
                'has_support': client.has_support,
                'support_schedule': client.support_schedule or 'lv'
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/api/client/<int:client_id>/monthly_hours')
@login_required
def api_client_monthly_hours(client_id):
    """API para obtener las horas de trabajo registradas para un cliente en el mes actual"""
    try:
        today = date.today()
        month_start = today.replace(day=1)
        
        tasks = Task.query.filter(
            Task.client_id == client_id,
            Task.status == 'Completado',
            Task.date >= month_start,
            Task.date <= today
        ).all()
        
        total_minutes = 0
        work_entries = []
        
        for task in tasks:
            # Calcular duración desde work_duration si está disponible
            if task.work_duration:
                try:
                    parts = task.work_duration.split(':')
                    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                    mins = h * 60 + m + (1 if s >= 30 else 0)
                    total_minutes += mins
                    work_entries.append({
                        'date': task.date.strftime('%d/%m/%Y') if task.date else None,
                        'tech': task.tech.username if task.tech else 'N/A',
                        'duration': task.work_duration,
                        'service': task.service_type.name if task.service_type else 'N/A',
                        'description': task.description or ''
                    })
                except:
                    pass
            elif task.start_time and task.end_time:
                try:
                    sh, sm = map(int, task.start_time.split(':'))
                    eh, em = map(int, task.end_time.split(':'))
                    mins = (eh * 60 + em) - (sh * 60 + sm)
                    if mins > 0:
                        total_minutes += mins
                        work_entries.append({
                            'date': task.date.strftime('%d/%m/%Y') if task.date else None,
                            'tech': task.tech.username if task.tech else 'N/A',
                            'duration': f"{mins//60:02d}:{mins%60:02d}:00",
                            'service': task.service_type.name if task.service_type else 'N/A',
                            'description': task.description or ''
                        })
                except:
                    pass
        
        total_hours = total_minutes // 60
        remaining_minutes = total_minutes % 60
        
        return jsonify({
            'success': True,
            'month': today.strftime('%B %Y'),
            'total_hours': total_hours,
            'total_minutes': remaining_minutes,
            'total_formatted': f"{total_hours}h {remaining_minutes}min",
            'entries': work_entries,
            'num_visits': len(work_entries)
        })
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/api/client_work_hours/<int:client_id>')
@login_required
def api_client_work_hours_alias(client_id):
    """Horas de trabajo del cliente este mes — incluye partes presenciales y soporte remoto"""
    try:
        today = date.today()
        month_start = today.replace(day=1)

        tasks = Task.query.filter(
            Task.client_id == client_id,
            Task.status == 'Completado',
            Task.date >= month_start,
            Task.date <= today
        ).all()

        total_minutes = 0
        task_list = []

        for task in tasks:
            duration_str = '—'
            mins = 0

            # 1) Intentar work_duration en formato HH:MM:SS
            if task.work_duration:
                wd = task.work_duration.strip()
                # Formato HH:MM:SS
                if wd.count(':') == 2:
                    try:
                        parts = wd.split(':')
                        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                        mins = h * 60 + m + (1 if s >= 30 else 0)
                        duration_str = f"{h:02d}:{m:02d}:{s:02d}"
                    except Exception:
                        pass
                # Formato "Xh YYmin" generado por tareas remotas
                elif 'h' in wd or 'min' in wd:
                    try:
                        import re as _re
                        h_match = _re.search(r'(\d+)\s*h', wd)
                        m_match = _re.search(r'(\d+)\s*min', wd)
                        h_val = int(h_match.group(1)) if h_match else 0
                        m_val = int(m_match.group(1)) if m_match else 0
                        mins = h_val * 60 + m_val
                        duration_str = f"{h_val:02d}:{m_val:02d}:00"
                    except Exception:
                        pass

            # 2) Fallback: remote_support_hours (float guardado al completar remota)
            if mins == 0 and task.remote_support_hours:
                try:
                    total_secs = int(task.remote_support_hours * 3600)
                    h_val = total_secs // 3600
                    m_val = (total_secs % 3600) // 60
                    s_val = total_secs % 60
                    mins = h_val * 60 + m_val + (1 if s_val >= 30 else 0)
                    duration_str = f"{h_val:02d}:{m_val:02d}:{s_val:02d}"
                except Exception:
                    pass

            # 3) Fallback: start_time / end_time
            if mins == 0 and task.start_time and task.end_time:
                try:
                    sh, sm = map(int, task.start_time.split(':'))
                    eh, em = map(int, task.end_time.split(':'))
                    mins = (eh * 60 + em) - (sh * 60 + sm)
                    if mins > 0:
                        duration_str = f"{mins // 60:02d}:{mins % 60:02d}:00"
                    else:
                        mins = 0
                except Exception:
                    pass

            if mins > 0:
                total_minutes += mins

            svc_name = task.service_type.name if task.service_type else ('Soporte Remoto' if task.is_remote else '—')
            task_list.append({
                'date': task.date.strftime('%d/%m/%Y') if task.date else '—',
                'tech': task.tech.username if task.tech else 'N/A',
                'service': svc_name,
                'duration': duration_str,
                'is_remote': bool(task.is_remote)
            })

        h_total = total_minutes // 60
        m_total = total_minutes % 60
        total_str = f"{h_total}h {m_total}min" if (h_total > 0 or m_total > 0) else '0h'

        return jsonify({
            'success': True,
            'total_hours': total_str,
            'month': today.strftime('%B %Y'),
            'tasks': task_list
        })
    except Exception as e:
        print(f"Error client_work_hours: {e}")
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/api/client/<int:client_id>/service_history')
@login_required
def api_client_service_history(client_id):
    """Devuelve el historial completo de servicios (partes) realizados para un cliente"""
    try:
        client = Client.query.get_or_404(client_id)

        # Filtros opcionales por año/mes desde query params
        year  = request.args.get('year',  type=int)
        month = request.args.get('month', type=int)
        status_filter = request.args.get('status', 'all')  # 'all', 'Completado', 'Pendiente'

        query = Task.query.filter(Task.client_id == client_id)

        if status_filter != 'all':
            query = query.filter(Task.status == status_filter)

        if year:
            query = query.filter(db.extract('year', Task.date) == year)
        if month:
            query = query.filter(db.extract('month', Task.date) == month)

        tasks = query.order_by(Task.date.desc()).all()

        task_list = []
        for task in tasks:
            # Calcular duración
            duration_str = '—'
            if task.work_duration:
                duration_str = task.work_duration
            elif task.start_time and task.end_time:
                try:
                    sh, sm = map(int, task.start_time.split(':'))
                    eh, em = map(int, task.end_time.split(':'))
                    mins = (eh * 60 + em) - (sh * 60 + sm)
                    if mins > 0:
                        duration_str = f"{mins//60:02d}:{mins%60:02d}:00"
                except:
                    pass

            # Técnicos adicionales
            extra_techs = [tt.user.username for tt in task.extra_technicians if tt.user]

            task_list.append({
                'id':           task.id,
                'date':         task.date.strftime('%d/%m/%Y') if task.date else '—',
                'date_iso':     task.date.isoformat() if task.date else '',
                'tech':         task.tech.username if task.tech else 'Sin asignar',
                'extra_techs':  extra_techs,
                'service':      task.service_type.name if task.service_type else '—',
                'service_color': task.service_type.color if task.service_type else '#6c757d',
                'description':  task.description or '',
                'status':       task.status,
                'duration':     duration_str,
                'is_remote':    task.is_remote,
                'has_signature': bool(task.signature_data),
                'parts_text':   task.parts_text or '',
            })

        # Años disponibles para el filtro
        available_years = db.session.query(
            db.extract('year', Task.date).label('yr')
        ).filter(Task.client_id == client_id, Task.date != None).distinct().order_by(db.text('yr desc')).all()
        available_years = [int(r.yr) for r in available_years]

        return jsonify({
            'success': True,
            'client_name': client.name,
            'total': len(task_list),
            'tasks': task_list,
            'available_years': available_years,
        })
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)}), 500


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- ARCHIVOS ESTÁTICOS ---
@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    """Endpoint para descargar archivos adjuntos"""
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Verificar que el archivo existe
        if not os.path.exists(filepath):
            print(f"Archivo no encontrado: {filepath}")
            return jsonify({'error': 'Archivo no encontrado'}), 404
        
        # Verificar que el archivo está dentro del directorio de uploads (seguridad)
        upload_folder = os.path.abspath(app.config['UPLOAD_FOLDER'])
        requested_path = os.path.abspath(filepath)
        
        if not requested_path.startswith(upload_folder):
            print(f"Intento de acceso fuera del directorio de uploads: {requested_path}")
            return jsonify({'error': 'Acceso denegado'}), 403
        
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except Exception as e:
        print(f"Error al servir archivo {filename}: {str(e)}")
        return jsonify({'error': f'Error al servir el archivo: {str(e)}'}), 500

# --- ARRANQUE ---
def _run_migration(conn, sql, description=""):
    """Ejecuta una sentencia DDL de forma segura, con su propia transacción."""
    try:
        conn.execute(db.text(sql))
        conn.execute(db.text("COMMIT"))
        if description:
            print(f"✓ {description}")
    except Exception as e:
        try:
            conn.execute(db.text("ROLLBACK"))
        except Exception:
            pass
        err_str = str(e).lower()
        # Errores esperados: columna/tabla ya existe
        if any(x in err_str for x in ['already exists', 'duplicate column', 'ya existe']):
            if description:
                print(f"ℹ  Ya existe: {description} (ignorado)")
        else:
            print(f"⚠  Migración [{description}]: {e}")


def initialize_database():
    """Inicializar BD y migraciones. Se ejecuta siempre (gunicorn + python directo)."""
    with app.app_context():
        # 1. Crear todas las tablas definidas en los modelos (seguro, idempotente)
        db.create_all()
    
        # 2. Migraciones DDL — cada una en su propia transacción limpia
        is_pg = db.engine.dialect.name == 'postgresql'
        is_sqlite = db.engine.dialect.name == 'sqlite'

        with db.engine.connect() as conn:
            # Forzar autocommit desactivado y comenzar limpio
            conn.execute(db.text("ROLLBACK")) if is_pg else None

            # --- USER ---
            if is_pg:
                _run_migration(conn, 'DROP INDEX IF EXISTS ix_user_email', "Drop ix_user_email")
                _run_migration(conn, 'ALTER TABLE "user" DROP CONSTRAINT IF EXISTS uq_user_email', "Drop uq_user_email")
                _run_migration(conn, 'ALTER TABLE "user" DROP CONSTRAINT IF EXISTS user_email_key', "Drop user_email_key")
                _run_migration(conn, 'ALTER TABLE "user" ALTER COLUMN password_hash TYPE VARCHAR(512)', "Ampliar password_hash")

            # --- CLIENT ---
            _run_migration(conn, 'ALTER TABLE client ADD COLUMN link VARCHAR(500)', "client.link")
            _run_migration(conn, 'ALTER TABLE client ADD COLUMN support_schedule VARCHAR(5)', "client.support_schedule")
            # Hacer email y address opcionales en PostgreSQL (SQLite ya permite NULL)
            if is_pg:
                _run_migration(conn, 'ALTER TABLE client ALTER COLUMN email DROP NOT NULL', "client.email nullable")
                _run_migration(conn, 'ALTER TABLE client ALTER COLUMN address DROP NOT NULL', "client.address nullable")

            # --- STOCK ---
            _run_migration(conn, 'ALTER TABLE stock ADD COLUMN supplier VARCHAR(100)', "stock.supplier")

            # --- TASK: nuevas columnas ---
            _run_migration(conn, 'ALTER TABLE task ADD COLUMN work_duration VARCHAR(20)', "task.work_duration")
            _run_migration(conn, 'ALTER TABLE task ADD COLUMN created_by INTEGER DEFAULT NULL', "task.created_by")
            _run_migration(conn, 'ALTER TABLE task ADD COLUMN is_remote BOOLEAN DEFAULT FALSE', "task.is_remote")
            _run_migration(conn, 'ALTER TABLE task ADD COLUMN remote_support_hours FLOAT DEFAULT 0', "task.remote_support_hours")
            _run_migration(conn, 'ALTER TABLE task ADD COLUMN parte_transport_start VARCHAR(10)', "task.parte_transport_start")
            _run_migration(conn, 'ALTER TABLE task ADD COLUMN parte_arrival VARCHAR(10)', "task.parte_arrival")
            _run_migration(conn, 'ALTER TABLE task ADD COLUMN parte_work_start VARCHAR(10)', "task.parte_work_start")
            _run_migration(conn, 'ALTER TABLE task ADD COLUMN parte_work_end VARCHAR(10)', "task.parte_work_end")

            # --- TASK: hacer nullable tech_id y date en PostgreSQL ---
            if is_pg:
                _run_migration(conn, 'ALTER TABLE task ALTER COLUMN tech_id DROP NOT NULL', "task.tech_id nullable")
                _run_migration(conn, 'ALTER TABLE task ALTER COLUMN date DROP NOT NULL', "task.date nullable")
                # FK created_by (ignorar si ya existe)
                _run_migration(conn,
                    'ALTER TABLE task ADD CONSTRAINT fk_task_created_by '
                    'FOREIGN KEY (created_by) REFERENCES "user"(id) ON DELETE SET NULL',
                    "task.created_by FK")

            # --- PAYMENT_RECORD: is_paid ---
            _run_migration(conn, 'ALTER TABLE payment_record ADD COLUMN is_paid BOOLEAN NOT NULL DEFAULT FALSE', "payment_record.is_paid")

        print("✓ Migraciones completadas")

        # Usuarios de prueba
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(
                username='admin',
                email='admin@oslaprint.com',
                role='admin', 
                password_hash=generate_password_hash('Admin123!')
            ))
    
        # 2. Técnico de prueba
        if not User.query.filter_by(username='tecnico').first():
            db.session.add(User(
                username='tecnico',
                email='tecnico@oslaprint.com',
                role='tech', 
                password_hash=generate_password_hash('Tecnico123!')
            ))
    
        # Tipos de Servicio
        if ServiceType.query.count() == 0:
            servicios = [
                {'name': 'Avería', 'color': '#fd7e14'},
                {'name': 'Revisión', 'color': '#0d6efd'},
                {'name': 'Instalación', 'color': '#6f42c1'},
                {'name': 'Mantenimiento', 'color': '#ffc107'},
                {'name': 'Otros servicios', 'color': '#20c997'}
            ]
            for s in servicios:
                db.session.add(ServiceType(name=s['name'], color=s['color']))
    
        # Categorías de Stock
        if StockCategory.query.count() == 0:
            copiadoras = StockCategory(name='Copiadoras')
            cajones = StockCategory(name='Cajones')
            tpv = StockCategory(name='TPV')
            recicladores = StockCategory(name='Recicladores')
            consumibles = StockCategory(name='Consumibles')
        
            db.session.add_all([copiadoras, cajones, tpv, recicladores, consumibles])
            db.session.commit()
        
            cashlogy = StockCategory(name='Cashlogy', parent_id=cajones.id)
            cashkeeper = StockCategory(name='Cashkeeper', parent_id=cajones.id)
            atca = StockCategory(name='ATCA', parent_id=cajones.id)
        
            db.session.add_all([cashlogy, cashkeeper, atca])
            db.session.commit()
    
        # Productos de Stock
        if Stock.query.count() == 0:
            copiadoras_cat = StockCategory.query.filter_by(name='Copiadoras').first()
            tpv_cat = StockCategory.query.filter_by(name='TPV').first()
            recicladores_cat = StockCategory.query.filter_by(name='Recicladores').first()
            consumibles_cat = StockCategory.query.filter_by(name='Consumibles').first()
            cashlogy_cat = StockCategory.query.filter_by(name='Cashlogy').first()
            cashkeeper_cat = StockCategory.query.filter_by(name='Cashkeeper').first()
            atca_cat = StockCategory.query.filter_by(name='ATCA').first()
        
            stock_items = [
                {'name': 'Copiadora HP LaserJet Pro', 'category_id': copiadoras_cat.id if copiadoras_cat else None, 'quantity': 3, 'min_stock': 1, 'supplier': 'HP España'},
                {'name': 'Copiadora Canon imageRUNNER', 'category_id': copiadoras_cat.id if copiadoras_cat else None, 'quantity': 2, 'min_stock': 1, 'supplier': 'Canon Iberia'},
                {'name': 'Cajón Cashlogy 1500', 'category_id': cashlogy_cat.id if cashlogy_cat else None, 'quantity': 5, 'min_stock': 2, 'supplier': 'Glory Global'},
                {'name': 'Cajón Cashlogy 2500', 'category_id': cashlogy_cat.id if cashlogy_cat else None, 'quantity': 3, 'min_stock': 1, 'supplier': 'Glory Global'},
                {'name': 'Cajón Cashkeeper Pro', 'category_id': cashkeeper_cat.id if cashkeeper_cat else None, 'quantity': 4, 'min_stock': 2, 'supplier': 'Cashkeeper Systems'},
                {'name': 'Cajón Cashkeeper Lite', 'category_id': cashkeeper_cat.id if cashkeeper_cat else None, 'quantity': 2, 'min_stock': 1, 'supplier': 'Cashkeeper Systems'},
                {'name': 'Cajón ATCA Standard', 'category_id': atca_cat.id if atca_cat else None, 'quantity': 3, 'min_stock': 1, 'supplier': 'ATCA Solutions'},
                {'name': 'Cajón ATCA Pro', 'category_id': atca_cat.id if atca_cat else None, 'quantity': 2, 'min_stock': 1, 'supplier': 'ATCA Solutions'},
                {'name': 'TPV Táctil 15"', 'category_id': tpv_cat.id if tpv_cat else None, 'quantity': 6, 'min_stock': 2, 'supplier': 'Epson POS'},
                {'name': 'TPV Táctil 17"', 'category_id': tpv_cat.id if tpv_cat else None, 'quantity': 4, 'min_stock': 2, 'supplier': 'Epson POS'},
                {'name': 'Reciclador 1', 'category_id': recicladores_cat.id if recicladores_cat else None, 'quantity': 2, 'min_stock': 1, 'supplier': 'Gunnebo'},
                {'name': 'Toner Genérico Negro', 'category_id': consumibles_cat.id if consumibles_cat else None, 'quantity': 15, 'min_stock': 5, 'supplier': 'Suministros Office'},
                {'name': 'Toner Genérico Color', 'category_id': consumibles_cat.id if consumibles_cat else None, 'quantity': 10, 'min_stock': 5, 'supplier': 'Suministros Office'},
            ]
            for item in stock_items:
                db.session.add(Stock(**item))
    
        # Cliente de ejemplo
        if Client.query.count() == 0:
            db.session.add(Client(
                name='Cliente Ejemplo',
                phone='900123456',
                email='ejemplo@cliente.com',
                address='Calle Ejemplo 1, Madrid',
                has_support=True
            ))
        
        db.session.commit()


# Ejecutar migraciones siempre, tanto con gunicorn como con python directo
initialize_database()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)