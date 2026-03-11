import os
import requests
import json
import io
from flask import Flask, render_template, jsonify, request, send_file
from datetime import datetime
from werkzeug.utils import secure_filename
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import openpyxl

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN WHATSAPP META API
# ═══════════════════════════════════════════════════════════════
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.date import DateTrigger
    import pytz
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
    print("⚠️  [Scheduler] APScheduler no instalado. pip install apscheduler para activarlo.")

META_PHONE_NUMBER_ID = os.environ.get('META_PHONE_NUMBER_ID', None)
META_ACCESS_TOKEN = os.environ.get('META_ACCESS_TOKEN', None)
META_API_BASE_URL = "https://graph.instagram.com/v18.0"

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN BASE DE DATOS POSTGRESQL
# ═══════════════════════════════════════════════════════════════
DATABASE_URL = os.environ.get('DATABASE_URL', None)

def get_db_connection():
    """
    Obtiene una conexión a la BD PostgreSQL.
    Maneja la URL de conexión de Render que usa postgresql:// 
    """
    try:
        if not DATABASE_URL:
            print("⚠️  DATABASE_URL no configurada")
            return None
        
        # Render usa postgres:// pero psycopg2 necesita postgresql://
        db_url = DATABASE_URL.replace('postgres://', 'postgresql://')
        conn = psycopg2.connect(db_url)
        return conn
    except psycopg2.Error as e:
        print(f"❌ Error conectando a BD: {e}")
        return None

def init_db():
    """
    Inicializa la base de datos si no existe.
    Crea todas las tablas necesarias.
    """
    conn = get_db_connection()
    if not conn:
        print("❌ No se pudo conectar a la BD")
        return False
    
    try:
        cur = conn.cursor()
        
        # Tabla de usuarios
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                name VARCHAR(200) NOT NULL,
                email VARCHAR(200),
                phone VARCHAR(20),
                role VARCHAR(50),
                departamento VARCHAR(100),
                color VARCHAR(20),
                active BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de clientes
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                email VARCHAR(200),
                phone VARCHAR(20),
                address VARCHAR(300),
                city VARCHAR(100),
                cif VARCHAR(20),
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de conversaciones (for organization)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                user1_id INTEGER NOT NULL REFERENCES users(id),
                user2_id INTEGER NOT NULL REFERENCES users(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user1_id, user2_id)
            )
        """)
        
        # Tabla de mensajes privados
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id),
                sender_id INTEGER NOT NULL REFERENCES users(id),
                text TEXT,
                read BOOLEAN DEFAULT false,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de adjuntos en mensajes
        cur.execute("""
            CREATE TABLE IF NOT EXISTS message_attachments (
                id SERIAL PRIMARY KEY,
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                filename VARCHAR(300) NOT NULL,
                file_size INTEGER,
                file_type VARCHAR(100),
                file_data BYTEA NOT NULL,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de mensajes en chat general
        cur.execute("""
            CREATE TABLE IF NOT EXISTS general_chat_messages (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de eventos/citas
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                assigned_to INTEGER REFERENCES users(id),
                client_name VARCHAR(200),
                service VARCHAR(200),
                event_date DATE,
                time_start TIME,
                time_end TIME,
                notes TEXT,
                private BOOLEAN DEFAULT false,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        print("✅ Base de datos inicializada correctamente")
        return True
    except psycopg2.Error as e:
        print(f"❌ Error inicializando BD: {e}")
        return False
    finally:
        cur.close()
        conn.close()

# ═══════════════════════════════════════════════════════════════
# INICIALIZAR FLASK Y SCHEDULER
# ═══════════════════════════════════════════════════════════════
app = Flask(__name__, template_folder='templates')
app.config['ENV'] = os.environ.get('FLASK_ENV', 'production')
app.config['DEBUG'] = False if app.config['ENV'] == 'production' else True
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file upload

UPLOAD_FOLDER = '/tmp/uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'zip'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

scheduler = None
if SCHEDULER_AVAILABLE:
    scheduler = BackgroundScheduler(timezone='Europe/Madrid')
    scheduler.start()
    print("✅ [Scheduler] APScheduler iniciado correctamente.")

# Inicializar BD
with app.app_context():
    init_db()

# ═══════════════════════════════════════════════════════════════
# RUTAS PRINCIPALES
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def home():
    """Ruta principal - Servir GestióPro"""
    return render_template('index3.html')

@app.route('/api/status')
def api_status():
    """Endpoint para verificar estado de la API"""
    db_ready = get_db_connection() is not None
    return jsonify({
        'status': 'ok',
        'app': 'GestióPro',
        'version': '3.0',
        'timestamp': datetime.now().isoformat(),
        'database_ready': db_ready,
        'whatsapp_ready': bool(META_ACCESS_TOKEN and META_PHONE_NUMBER_ID),
        'scheduler_ready': SCHEDULER_AVAILABLE and scheduler is not None,
    })

@app.route('/api/health')
def api_health():
    """Endpoint de health check para Render"""
    return jsonify({'status': 'healthy', 'service': 'gestionpro'}), 200

# ═══════════════════════════════════════════════════════════════
# API MENSAJERÍA PRIVADA (NEW - Database backed)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/messages/<int:other_user_id>', methods=['GET'])
def get_messages(other_user_id):
    """
    Obtiene todos los mensajes entre el usuario actual y otro usuario.
    Se debe enviar el user_id del usuario actual en la sesión o headers.
    
    Returns: { messages: [{ id, sender_id, text, attachments, created_at, read }] }
    """
    current_user_id = request.headers.get('X-User-ID', type=int)
    
    if not current_user_id:
        return jsonify({'ok': False, 'error': 'No user ID provided'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'ok': False, 'error': 'Database unavailable'}), 503
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Obtener o crear conversación
        cur.execute("""
            SELECT id FROM conversations 
            WHERE (user1_id = %s AND user2_id = %s) 
               OR (user1_id = %s AND user2_id = %s)
        """, (current_user_id, other_user_id, other_user_id, current_user_id))
        
        conv_result = cur.fetchone()
        if not conv_result:
            # Crear nueva conversación
            cur.execute("""
                INSERT INTO conversations (user1_id, user2_id)
                VALUES (%s, %s)
                RETURNING id
            """, (min(current_user_id, other_user_id), max(current_user_id, other_user_id)))
            conv_id = cur.fetchone()['id']
            conn.commit()
        else:
            conv_id = conv_result['id']
        
        # Obtener mensajes
        cur.execute("""
            SELECT m.id, m.sender_id, m.text, m.read, m.created_at,
                   array_agg(
                       json_build_object(
                           'id', ma.id,
                           'filename', ma.filename,
                           'file_size', ma.file_size,
                           'file_type', ma.file_type
                       ) ORDER BY ma.id
                   ) FILTER (WHERE ma.id IS NOT NULL) as attachments
            FROM messages m
            LEFT JOIN message_attachments ma ON m.id = ma.message_id
            WHERE m.conversation_id = %s
            GROUP BY m.id
            ORDER BY m.created_at ASC
        """, (conv_id,))
        
        messages = cur.fetchall()
        
        # Marcar como leídos los que no son del usuario actual
        cur.execute("""
            UPDATE messages SET read = true 
            WHERE conversation_id = %s AND sender_id != %s
        """, (conv_id, current_user_id))
        conn.commit()
        
        return jsonify({
            'ok': True,
            'conversation_id': conv_id,
            'messages': [dict(m) for m in messages]
        })
    
    except psycopg2.Error as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/messages', methods=['POST'])
def send_message():
    """
    Envía un mensaje privado entre dos usuarios.
    
    Body JSON:
    {
        "to_user_id": 2,
        "text": "Hola, ¿qué tal?",
        "attachments": [{ "filename": "doc.pdf", "file_data": "base64..." }]  // optional
    }
    
    Returns: { ok: true, message_id: 123, created_at: "..." }
    """
    current_user_id = request.headers.get('X-User-ID', type=int)
    
    if not current_user_id:
        return jsonify({'ok': False, 'error': 'No user ID provided'}), 400
    
    data = request.get_json(silent=True) or {}
    to_user_id = data.get('to_user_id')
    text = data.get('text', '').strip()
    attachments = data.get('attachments', [])
    
    if not to_user_id or (not text and not attachments):
        return jsonify({'ok': False, 'error': 'Missing required fields'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'ok': False, 'error': 'Database unavailable'}), 503
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Obtener o crear conversación
        cur.execute("""
            SELECT id FROM conversations 
            WHERE (user1_id = %s AND user2_id = %s) 
               OR (user1_id = %s AND user2_id = %s)
        """, (current_user_id, to_user_id, to_user_id, current_user_id))
        
        conv_result = cur.fetchone()
        if not conv_result:
            cur.execute("""
                INSERT INTO conversations (user1_id, user2_id)
                VALUES (%s, %s)
                RETURNING id
            """, (min(current_user_id, to_user_id), max(current_user_id, to_user_id)))
            conv_id = cur.fetchone()['id']
        else:
            conv_id = conv_result['id']
        
        # Insertar mensaje
        cur.execute("""
            INSERT INTO messages (conversation_id, sender_id, text, read)
            VALUES (%s, %s, %s, true)
            RETURNING id, created_at
        """, (conv_id, current_user_id, text or None))
        
        msg_result = cur.fetchone()
        message_id = msg_result['id']
        created_at = msg_result['created_at']
        
        # Insertar adjuntos si existen
        if attachments:
            for att in attachments:
                file_data = att.get('file_data', '')
                filename = secure_filename(att.get('filename', 'file'))
                file_type = att.get('file_type', '')
                file_size = att.get('file_size', 0)
                
                # Convertir base64 a bytes
                if isinstance(file_data, str):
                    import base64
                    try:
                        file_bytes = base64.b64decode(file_data)
                    except:
                        file_bytes = file_data.encode()
                else:
                    file_bytes = file_data
                
                cur.execute("""
                    INSERT INTO message_attachments 
                    (message_id, filename, file_size, file_type, file_data)
                    VALUES (%s, %s, %s, %s, %s)
                """, (message_id, filename, len(file_bytes), file_type, file_bytes))
        
        conn.commit()
        
        return jsonify({
            'ok': True,
            'message_id': message_id,
            'created_at': created_at.isoformat() if created_at else datetime.now().isoformat()
        })
    
    except psycopg2.Error as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/messages/<int:message_id>/attachments/<int:att_id>/download', methods=['GET'])
def download_attachment(message_id, att_id):
    """
    Descarga un adjunto específico de un mensaje.
    """
    current_user_id = request.headers.get('X-User-ID', type=int)
    
    if not current_user_id:
        return jsonify({'ok': False, 'error': 'No user ID provided'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'ok': False, 'error': 'Database unavailable'}), 503
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Verificar que el usuario tiene acceso a este adjunto
        cur.execute("""
            SELECT ma.filename, ma.file_data, ma.file_type
            FROM message_attachments ma
            JOIN messages m ON ma.message_id = m.id
            JOIN conversations c ON m.conversation_id = c.id
            WHERE ma.id = %s 
              AND (c.user1_id = %s OR c.user2_id = %s)
        """, (att_id, current_user_id, current_user_id))
        
        att = cur.fetchone()
        if not att:
            return jsonify({'ok': False, 'error': 'Attachment not found'}), 404
        
        return send_file(
            io.BytesIO(att['file_data']),
            mimetype=att['file_type'] or 'application/octet-stream',
            as_attachment=True,
            download_name=att['filename']
        )
    
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ═══════════════════════════════════════════════════════════════
# API CLIENTES
# ═══════════════════════════════════════════════════════════════

@app.route('/api/clients', methods=['GET'])
def get_clients():
    """Obtiene todos los clientes"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'ok': False, 'error': 'Database unavailable'}), 503
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM clients ORDER BY created_at DESC")
        clients = cur.fetchall()
        return jsonify({'ok': True, 'clients': [dict(c) for c in clients]})
    except psycopg2.Error as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/clients', methods=['POST'])
def create_client():
    """Crea un nuevo cliente"""
    data = request.get_json(silent=True) or {}
    
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    
    if not name or not phone:
        return jsonify({'ok': False, 'error': 'Name and phone are required'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'ok': False, 'error': 'Database unavailable'}), 503
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            INSERT INTO clients (name, phone, email)
            VALUES (%s, %s, %s)
            RETURNING id, created_at
        """, (name, phone, email or None))
        result = cur.fetchone()
        conn.commit()
        
        return jsonify({'ok': True, 'client': dict(result)})
    except psycopg2.Error as e:
        conn.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/clients/import/excel', methods=['POST'])
def import_clients_excel():
    """
    Importa clientes desde un archivo Excel.
    El Excel debe tener 2 columnas: nombre (col 1) y teléfono (col 2)
    
    Returns: { ok: true, imported: 5, duplicates: 2 }
    """
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400
    
    file = request.files['file']
    if not file.filename.endswith(('.xls', '.xlsx')):
        return jsonify({'ok': False, 'error': 'Only .xls and .xlsx files are allowed'}), 400
    
    try:
        # Leer Excel
        df = pd.read_excel(file, sheet_name=0, header=None)
        
        # Esperar exactamente 2 columnas (nombre, teléfono)
        if df.shape[1] < 2:
            return jsonify({'ok': False, 'error': 'Excel debe tener al menos 2 columnas'}), 400
        
        # Extraer columnas
        names = df.iloc[:, 0].astype(str).str.strip()
        phones = df.iloc[:, 1].astype(str).str.strip()
        
        conn = get_db_connection()
        if not conn:
            return jsonify({'ok': False, 'error': 'Database unavailable'}), 503
        
        cur = conn.cursor()
        imported = 0
        duplicates = 0
        
        for name, phone in zip(names, phones):
            name = name.strip()
            phone = phone.strip()
            
            if not name or not phone or name.lower() == 'nan':
                continue
            
            # Verificar duplicados
            cur.execute("SELECT id FROM clients WHERE name = %s", (name,))
            if cur.fetchone():
                duplicates += 1
                continue
            
            # Insertar
            try:
                cur.execute(
                    "INSERT INTO clients (name, phone) VALUES (%s, %s)",
                    (name, phone)
                )
                imported += 1
            except psycopg2.IntegrityError:
                conn.rollback()
                duplicates += 1
                continue
        
        conn.commit()
        
        return jsonify({
            'ok': True,
            'imported': imported,
            'duplicates': duplicates,
            'total_processed': len(names)
        })
    
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        if conn:
            cur.close()
            conn.close()

# ═══════════════════════════════════════════════════════════════
# API WHATSAPP (Meta Cloud API)
# ═══════════════════════════════════════════════════════════════

def get_meta_headers():
    """Devuelve headers necesarios para autenticarse con Meta API."""
    if not META_ACCESS_TOKEN:
        return None, "Token de acceso Meta no configurado"
    return {
        'Authorization': f'Bearer {META_ACCESS_TOKEN}',
        'Content-Type': 'application/json'
    }, None

def send_whatsapp_via_meta(to_phone: str, message: str) -> tuple:
    """Envía un mensaje de WhatsApp via Meta Cloud API"""
    headers, err = get_meta_headers()
    if err:
        return False, None, err

    # Normalizar número
    phone = to_phone.strip()
    if not phone.startswith('+'):
        if phone.startswith('34'):
            phone = '+' + phone
        elif len(phone) == 9 and phone[0] in '69':
            phone = '+34' + phone
        else:
            phone = '+' + phone.lstrip('0')

    payload = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': phone,
        'type': 'text',
        'text': {
            'preview_url': False,
            'body': message
        }
    }

    try:
        url = f"{META_API_BASE_URL}/{META_PHONE_NUMBER_ID}/messages"
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code in [200, 201]:
            data = response.json()
            msg_id = data.get('messages', [{}])[0].get('id', 'unknown')
            print(f"✅ [Meta/WA] Enviado a {phone} · Message ID: {msg_id}")
            return True, msg_id, None
        else:
            error_detail = response.json().get('error', {}).get('message', 'Error desconocido')
            print(f"❌ [Meta/WA] Error {response.status_code}: {error_detail}")
            return False, None, f"Error {response.status_code}: {error_detail}"
    except Exception as e:
        print(f"❌ [Meta/WA] Error: {str(e)}")
        return False, None, str(e)

@app.route('/api/whatsapp/send', methods=['POST'])
def send_whatsapp():
    """Envía un mensaje de WhatsApp inmediatamente"""
    data = request.get_json(silent=True) or {}
    to_phone = data.get('to', '').strip()
    message = data.get('message', '').strip()

    if not to_phone or not message:
        return jsonify({'ok': False, 'error': 'Falta el campo "to" o "message"'}), 400

    success, msg_id, err = send_whatsapp_via_meta(to_phone, message)
    
    if success:
        return jsonify({'ok': True, 'message_id': msg_id})
    else:
        return jsonify({
            'ok': False, 
            'error': err,
            'configured': bool(META_ACCESS_TOKEN and META_PHONE_NUMBER_ID)
        }), 503

@app.route('/api/whatsapp/status', methods=['GET'])
def whatsapp_status():
    """Comprueba si Meta API está configurada"""
    meta_ok = bool(META_ACCESS_TOKEN and META_PHONE_NUMBER_ID)
    
    reasons = []
    if not meta_ok:
        if not META_ACCESS_TOKEN:
            reasons.append("META_ACCESS_TOKEN no configurado")
        if not META_PHONE_NUMBER_ID:
            reasons.append("META_PHONE_NUMBER_ID no configurado")
    
    return jsonify({
        'meta_ready': meta_ok,
        'fully_ready': meta_ok,
        'reason': ' · '.join(reasons) if reasons else 'Todo configurado correctamente'
    })

# ═══════════════════════════════════════════════════════════════
# MANEJO DE ERRORES
# ═══════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(error):
    """Manejar errores 404 - Servir la app en lugar de error"""
    return render_template('index3.html'), 200

@app.errorhandler(500)
def server_error(error):
    """Manejar errores 500"""
    return jsonify({'error': 'Internal server error', 'details': str(error)}), 500

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE PUERTO Y HOST
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    host = '0.0.0.0'
    app.run(
        host=host,
        port=port,
        debug=app.config['DEBUG'],
        use_reloader=False
    )