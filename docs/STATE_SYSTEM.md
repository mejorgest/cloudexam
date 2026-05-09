s# 🧠 Sistema de State - Inspirado en Google ADK

## ¿Qué es el State?

El **State** es la "memoria de trabajo" del agente - donde guarda información importante durante y entre conversaciones. Es diferente del historial de mensajes (que es inmutable).

## 🎯 Características Principales

### 1. **Múltiples Alcances (Scopes)**

El state soporta 4 tipos de alcance mediante prefijos:

| Prefijo | Alcance | Persistencia | Uso |
|---------|---------|--------------|-----|
| *ninguno* | **Sesión** | Solo en esta conversación | Datos temporales de la conversación |
| `user:` | **Usuario** | Entre sesiones del mismo usuario | Preferencias del usuario |
| `app:` | **Aplicación** | Global, todos los usuarios | Contadores, datos compartidos |
| `temp:` | **Temporal** | Se elimina tras cada turno | Cálculos intermedios |

### 2. **Delta Tracking**

El state implementa un sistema de "delta" que rastrea cambios pendientes antes de commit:
- Solo los cambios se persisten (eficiente)
- Rollback disponible si algo falla
- Commit automático después de cada turno

### 3. **Persistencia Automática**

- Se guarda automáticamente en SQLite después de cada ejecución
- Separado por scopes en la base de datos
- Merge inteligente de múltiples scopes

## 📖 Uso desde el Código del LLM

El agente puede usar `state` directamente en el código generado:

```python
# ============================================
# ESTADO DE SESIÓN (session scope)
# ============================================

# Guardar información de la conversación actual
state["current_step"] = "payment"
state["cart_items"] = ["laptop", "mouse"]
state["order_total"] = 1299.99

# Leer estado
items = state.get("cart_items", [])  # Con valor por defecto
total = state["order_total"]  # Acceso directo

# ============================================
# ESTADO DE USUARIO (user: scope)
# ============================================

# Preferencias que persisten entre sesiones
state["user:preferred_language"] = "es"
state["user:theme"] = "dark"
state["user:notification_settings"] = {"email": True, "sms": False}

# Leer preferencias
lang = state.get("user:preferred_language", "en")

# ============================================
# ESTADO DE APLICACIÓN (app: scope)
# ============================================

# Datos compartidos globalmente
current_count = state.get("app:total_requests", 0)
state["app:total_requests"] = current_count + 1

state["app:feature_flags"] = {"new_ui": True}

# ============================================
# ESTADO TEMPORAL (temp: scope)
# ============================================

# Se elimina automáticamente al final del turno
state["temp:intermediate_calculation"] = sum([1, 2, 3, 4, 5])
state["temp:api_response_cache"] = api_result

# Usar en el mismo turno
result = state["temp:intermediate_calculation"]
print(f"Result: {result}")
```

## 🔧 Uso desde Python (API)

### Inicializar Agente con State

```python
from Agent.programmatic_agent import ProgrammaticMCPAgent

# Crear agente con user_id para habilitar user-scoped state
agent = ProgrammaticMCPAgent(
    db_path="./data/conversations.db",
    user_id="user_12345",  # Opcional: habilita user-scoped state
    verbose=False
)
```

### Acceder al State Programáticamente

```python
# Obtener el objeto State completo
state = agent.get_state()

# Leer valores
nombre = agent.get_state_value("user:name", default="Guest")

# Establecer valores
agent.set_state_value("custom_key", "custom_value")

# Ver resumen del estado
summary = agent.get_state_summary()
print(summary)
# {
#     "session_id": "abc-123",
#     "user_id": "user_12345",
#     "total_keys": 15,
#     "session_keys": 10,
#     "user_keys": 3,
#     "app_keys": 2,
#     "temp_keys": 0,
#     "has_pending_changes": True,
#     "pending_changes_count": 3
# }
```

### Limpiar State

```python
# Limpiar estado de sesión
agent.clear_state(scope="session")

# Limpiar estado de usuario
agent.clear_state(scope="user")

# Limpiar estado temporal
agent.clear_state(scope="temp")

# Limpiar todo el estado de sesión (por defecto)
agent.clear_state()
```

## 🗄️ Arquitectura de Persistencia

### Tabla State en SQLite

```sql
CREATE TABLE state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,              -- session_id, user_id, o "__app__"
    scope TEXT NOT NULL,                   -- 'session', 'user', o 'app'
    state_data TEXT NOT NULL DEFAULT '{}', -- JSON con los datos
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, scope)
);
```

### Cómo Se Persiste

1. **Durante la ejecución**: Los cambios se acumulan en `state._delta`
2. **Al finalizar el turno**: 
   - Se llama a `state_manager.save_state(state)`
   - Los cambios se separan por scope
   - Se hace UPSERT en SQLite (merge con estado existente)
   - Se limpia el delta y el temp state

### Ejemplo de Flujo

```
Usuario: "Mi nombre es Juan y me gusta el modo oscuro"

┌─────────────────────────────────────┐
│ 1. LLM genera código                │
│    state["name"] = "Juan"           │
│    state["user:theme"] = "dark"     │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 2. Código se ejecuta                │
│    - Cambios van a state._delta     │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 3. Al terminar el turno             │
│    - state_manager.save_state()     │
│    - Separa por scope:              │
│      • session: {"name": "Juan"}    │
│      • user: {"user:theme": "dark"} │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│ 4. Persistencia en DB               │
│    - UPSERT en tabla state          │
│    - Commit SQL                     │
│    - state._delta.clear()           │
└─────────────────────────────────────┘
```

## 🔄 Ciclo de Vida del State

```
┌──────────────────────────────────────────┐
│ CARGAR STATE                             │
│ - Merge: app + user + session           │
│ - Prioridad: session > user > app       │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│ CÓDIGO EJECUTA                           │
│ - state["key"] = value                   │
│ - Cambios → _delta                       │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│ COMMIT AUTOMÁTICO                        │
│ - Separar por scope                      │
│ - Guardar en DB                          │
│ - _delta → _value                        │
│ - Limpiar temp:*                         │
└──────────────────────────────────────────┘
```

## 📊 Ejemplos Prácticos

### Ejemplo 1: Carrito de Compras

```python
# Usuario agrega items al carrito
state["cart_items"] = state.get("cart_items", [])
state["cart_items"].append({"name": "Laptop", "price": 999})

# En otro turno, el agente recuerda el carrito
items = state.get("cart_items", [])
total = sum(item["price"] for item in items)
print(f"Total: ${total}")
```

### Ejemplo 2: Progreso Multi-Paso

```python
# Paso 1: Recopilar información
state["booking_step"] = 1
state["user_destination"] = "París"

# Paso 2: Seleccionar fechas
if state.get("booking_step") == 1:
    state["booking_step"] = 2
    state["travel_dates"] = {"start": "2025-06-01", "end": "2025-06-07"}

# Paso 3: Confirmar
if state.get("booking_step") == 2:
    state["booking_confirmed"] = True
    destination = state["user_destination"]
    dates = state["travel_dates"]
    print(f"Booking confirmed: {destination} from {dates['start']} to {dates['end']}")
```

### Ejemplo 3: Preferencias Persistentes

```python
# Primera sesión
state["user:notification_time"] = "09:00"
state["user:timezone"] = "America/Argentina/Buenos_Aires"

# Nueva sesión (mismo usuario)
# Las preferencias user:* están disponibles automáticamente
time = state.get("user:notification_time", "08:00")
tz = state.get("user:timezone", "UTC")
print(f"Notificaciones configuradas para {time} {tz}")
```

## 🚀 Ventajas vs Historial de Mensajes

| Característica | State | Historial de Mensajes |
|----------------|-------|----------------------|
| **Mutabilidad** | ✅ Mutable | ❌ Inmutable |
| **Estructura** | ✅ Datos estructurados (dict) | ❌ Texto plano |
| **Búsqueda** | ✅ Acceso directo por clave | ❌ Búsqueda lineal |
| **Persistencia** | ✅ Separada por scope | ❌ Todo junto |
| **Eficiencia** | ✅ Delta tracking | ❌ Guardar todo |
| **Alcance** | ✅ Session/User/App | ❌ Solo sesión |

## 🎓 Mejores Prácticas

1. **Usa el scope correcto**:
   - Session: Datos de la conversación actual
   - User: Preferencias que persisten
   - App: Datos globales
   - Temp: Cálculos intermedios

2. **Nombres descriptivos**:
   ```python
   # ✅ Bueno
   state["user:preferred_language"] = "es"
   
   # ❌ Malo
   state["user:lang"] = "es"
   ```

3. **Valores por defecto**:
   ```python
   # ✅ Siempre usa get() con default
   count = state.get("app:request_count", 0) + 1
   
   # ❌ Puede fallar si la clave no existe
   count = state["app:request_count"] + 1
   ```

4. **Limpiar temp state**:
   - Los temp: se limpian automáticamente
   - Úsalos para datos que solo necesitas en un turno

## 🔍 Debugging

```python
# Ver resumen del estado
summary = agent.get_state_summary()
print(json.dumps(summary, indent=2))

# Ver todo el estado
state_dict = agent.get_state().to_dict()
print(json.dumps(state_dict, indent=2))

# Ver solo delta (cambios pendientes)
delta = agent.get_state().get_delta()
print(f"Pending changes: {delta}")

# Verificar si hay cambios pendientes
if agent.get_state().has_delta():
    print("⚠️ Hay cambios sin guardar")
```

## 📚 Referencias

- Inspirado por: [Google ADK State Management](https://github.com/google/adk)
- Implementación: `Agent/state.py`
- Persistencia: `Agent/conversation_store.py` (métodos `*_state()`)
- Integración: `Agent/programmatic_agent.py`
