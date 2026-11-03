import os
import random
import string
import sqlite3
from functools import wraps
from urllib.parse import urlparse

from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "cambia-esta-clave-en-tu-archivo-env")

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("MONGO_DB", "playstart")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

if not MONGO_URI:
    raise RuntimeError("Falta MONGO_URI. Configúrala en el archivo .env (local) o en Environment Variables de Render.")
if not ADMIN_PASSWORD:
    raise RuntimeError("Falta ADMIN_PASSWORD. Configúrala en el archivo .env o en Render.")

mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = mongo_client[DB_NAME]
productos_col = db["productos"]
usuarios_col = db["usuarios"]
compras_col = db["compras"]
sistema_col = db["sistema"]

# Imágenes remotas: Steam CDN para títulos disponibles en Steam y Wikimedia para otros.
JUEGOS_INICIALES = [
    {"nombre": "Minecraft", "genero": "Sandbox", "dev": "Mojang Studios", "precio": 29.99, "img": "https://upload.wikimedia.org/wikipedia/en/5/51/Minecraft_cover.png"},
    {"nombre": "Genshin Impact", "genero": "ARPG", "dev": "HoYoverse", "precio": 0.00, "img": "https://upload.wikimedia.org/wikipedia/en/5/5d/Genshin_Impact_cover.jpg"},
    {"nombre": "Left 4 Dead 2", "genero": "FPS", "dev": "Valve", "precio": 9.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/550/header.jpg"},
    {"nombre": "Grand Theft Auto V", "genero": "Mundo Abierto", "dev": "Rockstar Games", "precio": 29.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/271590/header.jpg"},
    {"nombre": "Red Dead Redemption 2", "genero": "Mundo Abierto", "dev": "Rockstar Games", "precio": 59.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/1174180/header.jpg"},
    {"nombre": "Elden Ring", "genero": "ARPG", "dev": "FromSoftware", "precio": 59.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/1245620/header.jpg"},
    {"nombre": "The Witcher 3", "genero": "RPG", "dev": "CD PROJEKT RED", "precio": 39.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/292030/header.jpg"},
    {"nombre": "Cyberpunk 2077", "genero": "RPG", "dev": "CD PROJEKT RED", "precio": 59.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/1091500/header.jpg"},
    {"nombre": "Stardew Valley", "genero": "Simulación", "dev": "ConcernedApe", "precio": 14.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/413150/header.jpg"},
    {"nombre": "Terraria", "genero": "Sandbox", "dev": "Re-Logic", "precio": 9.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/105600/header.jpg"},
    {"nombre": "Call of Duty: Warzone", "genero": "FPS", "dev": "Activision", "precio": 0.00, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/1962663/header.jpg"},
    {"nombre": "Honkai: Star Rail", "genero": "RPG", "dev": "HoYoverse", "precio": 0.00, "img": "https://upload.wikimedia.org/wikipedia/en/b/b3/Honkai_Star_Rail_cover.jpg"},
    {"nombre": "Zenless Zone Zero", "genero": "ARPG", "dev": "HoYoverse", "precio": 0.00, "img": "https://upload.wikimedia.org/wikipedia/en/8/8e/Zenless_Zone_Zero_cover.jpg"},
    {"nombre": "Asphalt 8: Airborne", "genero": "Carreras", "dev": "Gameloft", "precio": 0.00, "img": "https://upload.wikimedia.org/wikipedia/en/4/4d/Asphalt_8_Airborne_cover.jpg"},
    {"nombre": "War Thunder", "genero": "Acción", "dev": "Gaijin Entertainment", "precio": 0.00, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/236390/header.jpg"},
    {"nombre": "Among Us", "genero": "Casual", "dev": "Innersloth", "precio": 4.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/945360/header.jpg"},
    {"nombre": "Hollow Knight", "genero": "Metroidvania", "dev": "Team Cherry", "precio": 14.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/367520/header.jpg"},
    {"nombre": "Forza Horizon 5", "genero": "Carreras", "dev": "Playground Games", "precio": 59.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/1551360/header.jpg"},
    {"nombre": "EA SPORTS FC 24", "genero": "Deportes", "dev": "Electronic Arts", "precio": 69.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/2195250/header.jpg"},
    {"nombre": "Resident Evil 4 Remake", "genero": "Terror", "dev": "Capcom", "precio": 39.99, "img": "https://cdn.cloudflare.steamstatic.com/steam/apps/2050650/header.jpg"},
]

_db_ready = False

def serializar_producto(doc):
    producto = dict(doc)
    producto["id"] = str(producto.pop("_id"))
    producto["desarrollador"] = producto.get("dev", "")
    return producto


def preparar_base_datos():
    """Crea índices, importa cuentas/compras antiguas una sola vez y carga el catálogo inicial."""
    global _db_ready
    if _db_ready:
        return
    mongo_client.admin.command("ping")
    usuarios_col.create_index([("username", ASCENDING)], unique=True)
    usuarios_col.create_index([("email", ASCENDING)], unique=True)
    productos_col.create_index([("nombre", ASCENDING)], unique=True)

    marcador = sistema_col.find_one({"_id": "migracion_sqlite_v1"})
    sqlite_path = os.path.join(os.path.dirname(__file__), "playstart.db")
    if not marcador and os.path.exists(sqlite_path):
        try:
            conn = sqlite3.connect(sqlite_path)
            conn.row_factory = sqlite3.Row
            antiguos = conn.execute("SELECT id, username, email, password FROM usuarios").fetchall()
            ids_usuarios = {}
            for viejo in antiguos:
                usuario = usuarios_col.find_one({"$or": [{"username": viejo["username"]}, {"email": viejo["email"]}]})
                if not usuario:
                    usuario_doc = {"username": viejo["username"], "email": viejo["email"], "password": viejo["password"]}
                    try:
                        resultado = usuarios_col.insert_one(usuario_doc)
                        usuario = {"_id": resultado.inserted_id, **usuario_doc}
                    except DuplicateKeyError:
                        usuario = usuarios_col.find_one({"username": viejo["username"]}) or usuarios_col.find_one({"email": viejo["email"]})
                if usuario:
                    ids_usuarios[viejo["id"]] = usuario["_id"]
            try:
                antiguas_compras = conn.execute("SELECT usuario_id, juego_nombre, codigo_activacion FROM compras").fetchall()
                for compra in antiguas_compras:
                    usuario_oid = ids_usuarios.get(compra["usuario_id"])
                    if usuario_oid and not compras_col.find_one({"usuario_id": usuario_oid, "codigo": compra["codigo_activacion"]}):
                        compras_col.insert_one({"usuario_id": usuario_oid, "juego_nombre": compra["juego_nombre"], "codigo": compra["codigo_activacion"], "precio": 0, "origen": "migracion"})
            except sqlite3.OperationalError:
                pass
            conn.close()
        except (sqlite3.Error, PyMongoError):
            app.logger.exception("No se pudieron importar todos los datos antiguos de SQLite.")
        sistema_col.update_one({"_id": "migracion_sqlite_v1"}, {"$set": {"completada": True}}, upsert=True)

    if productos_col.count_documents({}) == 0:
        productos_col.insert_many([{**juego, "creado": __import__("datetime").datetime.utcnow()} for juego in JUEGOS_INICIALES])
    _db_ready = True


@app.before_request
def verificar_conexion():
    try:
        preparar_base_datos()
    except Exception as error:
        app.logger.exception("Error conectando con MongoDB Atlas")
        return ("No se pudo conectar con MongoDB Atlas. Revisa MONGO_URI, Network Access y el usuario de base de datos. "
                "Detalle: " + str(error), 503)


def todos_los_juegos():
    return [serializar_producto(p) for p in productos_col.find().sort("nombre", ASCENDING)]


def buscar_juego(game_id):
    if not ObjectId.is_valid(game_id):
        return None
    doc = productos_col.find_one({"_id": ObjectId(game_id)})
    return serializar_producto(doc) if doc else None


def generar_codigo_key():
    def bloque():
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"PLST-{bloque()}-{bloque()}-{bloque()}"


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged"):
            return redirect(url_for("admin_login"))
        return func(*args, **kwargs)
    return wrapper


def url_imagen_valida(value):
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


@app.route("/")
def inicio():
    return render_template("index.html", juegos=todos_los_juegos()[:8])


@app.route("/tienda")
def tienda():
    return render_template("tienda.html", juegos=todos_los_juegos())


@app.route("/categorias")
def categorias():
    nombres = sorted(productos_col.distinct("genero"))
    paleta = ["#00d26a", "#ff4655", "#ff9900", "#00a2ff", "#9b51e0", "#ffc107", "#00f2fe", "#e056fd", "#f1c40f", "#e74c3c"]
    lista = [{"nombre": nombre, "color": paleta[i % len(paleta)]} for i, nombre in enumerate(nombres)]
    return render_template("categorias.html", categorias=lista)


@app.route("/categoria/<nombre_genero>")
def ver_categoria(nombre_genero):
    juegos = [serializar_producto(p) for p in productos_col.find({"genero": {"$regex": f"^{nombre_genero}$", "$options": "i"}})]
    return render_template("categoria_detalle.html", genero=nombre_genero, juegos=juegos)


@app.route("/ofertas")
def ofertas():
    nombres_oferta = ["Left 4 Dead 2", "The Witcher 3", "Grand Theft Auto V", "Cyberpunk 2077"]
    descuentos = {"Left 4 Dead 2": 0.80, "The Witcher 3": 0.80, "Grand Theft Auto V": 0.50, "Cyberpunk 2077": 0.50}
    ofertas_lista = []
    for nombre in nombres_oferta:
        juego = productos_col.find_one({"nombre": nombre})
        if juego:
            original = float(juego.get("precio", 0))
            descuento = descuentos[nombre]
            ofertas_lista.append({"nombre": nombre, "genero": juego.get("genero", ""), "precio_orig": original,
                                  "precio": round(original * (1 - descuento), 2), "descuento": f"-{int(descuento*100)}%",
                                  "img": juego.get("img", ""), "id": str(juego["_id"])})
    return render_template("ofertas.html", ofertas=ofertas_lista)


@app.route("/soporte")
def soporte():
    return render_template("soporte.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not username or not email or len(password) < 8:
            flash("Completa todos los campos. La contraseña debe tener al menos 8 caracteres.", "warning")
            return redirect(url_for("registro"))
        try:
            usuarios_col.insert_one({"username": username, "email": email, "password": generate_password_hash(password)})
            flash("Cuenta creada. Ya puedes iniciar sesión.", "success")
            return redirect(url_for("login"))
        except DuplicateKeyError:
            flash("El usuario o correo ya están registrados.", "danger")
    return render_template("registro.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        entrada = request.form.get("usuario", "").strip()
        usuario = usuarios_col.find_one({"$or": [{"username": {"$regex": f"^{entrada}$", "$options": "i"}}, {"email": entrada.lower()}]})
        if usuario and check_password_hash(usuario["password"], request.form.get("password", "")):
            session["user_id"] = str(usuario["_id"])
            session["username"] = usuario["username"]
            flash(f"Bienvenido de nuevo, {usuario['username']}.", "success")
            return redirect(url_for("inicio"))
        flash("Usuario o contraseña incorrectos.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("username", None)
    flash("Has cerrado sesión correctamente.", "info")
    return redirect(url_for("inicio"))


@app.route("/agregar_carrito/<game_id>")
def agregar_carrito(game_id):
    juego = buscar_juego(game_id)
    if juego:
        carrito = session.get("carrito", [])
        carrito.append({"id": juego["id"], "nombre": juego["nombre"], "genero": juego["genero"], "precio": float(juego["precio"]), "img": juego["img"]})
        session["carrito"] = carrito
        flash(f"{juego['nombre']} añadido al carrito.", "success")
    else:
        flash("Ese videojuego ya no está disponible.", "warning")
    return redirect(request.referrer or url_for("tienda"))


@app.route("/carrito")
def ver_carrito():
    carrito = session.get("carrito", [])
    total = round(sum(float(j["precio"]) for j in carrito), 2)
    return render_template("carrito.html", carrito=carrito, total=total)


@app.route("/vaciar_carrito", methods=["GET", "POST"])
def vaciar_carrito():
    session.pop("carrito", None)
    return redirect(url_for("ver_carrito"))


@app.route("/checkout", methods=["POST"])
def checkout():
    if "user_id" not in session:
        flash("Debes iniciar sesión o crear una cuenta para realizar la compra.", "warning")
        return redirect(url_for("login"))
    carrito = session.get("carrito", [])
    if not carrito:
        flash("Tu carrito está vacío.", "warning")
        return redirect(url_for("ver_carrito"))
    if not ObjectId.is_valid(session["user_id"]):
        session.clear()
        return redirect(url_for("login"))
    compras_realizadas = []
    for item in carrito:
        codigo = generar_codigo_key()
        compras_col.insert_one({"usuario_id": ObjectId(session["user_id"]), "juego_nombre": item["nombre"], "codigo": codigo, "precio": item["precio"]})
        compras_realizadas.append({"nombre": item["nombre"], "codigo": codigo, "precio": item["precio"]})
    session.pop("carrito", None)
    return render_template("confirmacion_compra.html", compras=compras_realizadas)


GENEROS_VIDEOJUEGOS = ['Acción', 'Aventura', 'ARPG', 'Carreras', 'Casual', 'Deportes', 'Estrategia', 'FPS', 'Lucha', 'Metroidvania', 'Mundo Abierto', 'Plataformas', 'Puzzle', 'Roguelike', 'RPG', 'Sandbox', 'Simulación', 'Survival', 'Terror']


# ---------------- ADMINISTRACIÓN ----------------
@app.route("/admin", methods=["GET"])
@admin_required
def admin_panel():
    return render_template("admin.html", juegos=todos_los_juegos(), generos=GENEROS_VIDEOJUEGOS)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "")
        password = request.form.get("password", "")
        if usuario == ADMIN_USER and password == ADMIN_PASSWORD:
            session["admin_logged"] = True
            flash("Sesión de administrador iniciada.", "success")
            return redirect(url_for("admin_panel"))
        flash("Credenciales de administrador incorrectas.", "danger")
    return render_template("admin_login.html")


@app.route("/admin/agregar", methods=["POST"])
@admin_required
def admin_agregar():
    nombre = request.form.get("nombre", "").strip()
    genero = request.form.get("genero", "").strip()
    dev = request.form.get("dev", "").strip()
    img = request.form.get("img", "").strip()
    try:
        precio = float(request.form.get("precio", ""))
        if precio < 0:
            raise ValueError
    except ValueError:
        flash("El precio debe ser un número igual o mayor que cero.", "warning")
        return redirect(url_for("admin_panel"))
    if not all([nombre, genero, dev, img]) or not url_imagen_valida(img):
        flash("Completa todos los campos y usa una URL de imagen que empiece con http:// o https://.", "warning")
        return redirect(url_for("admin_panel"))
    try:
        productos_col.insert_one({"nombre": nombre, "genero": genero, "dev": dev, "precio": round(precio, 2), "img": img})
        flash(f"{nombre} se agregó correctamente.", "success")
    except DuplicateKeyError:
        flash("Ya existe un videojuego con ese nombre.", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/editar/<game_id>", methods=["GET", "POST"])
@admin_required
def admin_editar(game_id):
    if not ObjectId.is_valid(game_id):
        abort(404)

    juego_doc = productos_col.find_one({"_id": ObjectId(game_id)})
    if not juego_doc:
        abort(404)

    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        genero = request.form.get("genero", "").strip()
        dev = request.form.get("dev", "").strip()
        img = request.form.get("img", "").strip()
        try:
            precio = float(request.form.get("precio", ""))
            if precio < 0:
                raise ValueError
        except ValueError:
            flash("El precio debe ser un número igual o mayor que cero.", "warning")
            return redirect(url_for("admin_editar", game_id=game_id))

        if not all([nombre, genero, dev, img]) or not url_imagen_valida(img):
            flash("Completa todos los campos y utiliza una URL de imagen válida (http:// o https://).", "warning")
            return redirect(url_for("admin_editar", game_id=game_id))

        try:
            productos_col.update_one(
                {"_id": ObjectId(game_id)},
                {"$set": {
                    "nombre": nombre,
                    "genero": genero,
                    "dev": dev,
                    "precio": round(precio, 2),
                    "img": img,
                    "actualizado": __import__("datetime").datetime.utcnow(),
                }}
            )
            flash(f"{nombre} se actualizó correctamente.", "success")
            return redirect(url_for("admin_panel"))
        except DuplicateKeyError:
            flash("Ya existe otro videojuego con ese nombre.", "danger")
            return redirect(url_for("admin_editar", game_id=game_id))

    return render_template("admin_editar.html", juego=serializar_producto(juego_doc), generos=GENEROS_VIDEOJUEGOS)


@app.route("/admin/eliminar/<game_id>", methods=["POST"])
@admin_required
def admin_eliminar(game_id):
    if not ObjectId.is_valid(game_id):
        abort(404)
    resultado = productos_col.delete_one({"_id": ObjectId(game_id)})
    flash("Videojuego eliminado." if resultado.deleted_count else "No se encontró ese videojuego.", "success" if resultado.deleted_count else "warning")
    return redirect(url_for("admin_panel"))


@app.route("/admin/logout")
@admin_required
def admin_logout():
    session.pop("admin_logged", None)
    flash("Sesión de administrador cerrada.", "info")
    return redirect(url_for("inicio"))


@app.errorhandler(PyMongoError)
def error_mongodb(error):
    app.logger.exception("Error de MongoDB")
    return "Ocurrió un problema al consultar MongoDB. Revisa la conexión de Atlas.", 503


if __name__ == "__main__":
    app.run(debug=True)
