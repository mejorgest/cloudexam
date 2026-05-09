# 🔄 Automatic State Enrichment - LangGraph Reducer Pattern

## ¿Qué es?

Sistema automático de enriquecimiento de contexto inspirado en **LangGraph reducers**. 

En lugar de requerir que el usuario escriba código para guardar información, el sistema **extrae automáticamente** información relevante del diálogo y la almacena en el state apropiado.

## 🏗️ Arquitectura

```
Usuario: "Hola, me llamo Jorge y vivo en San José"
Asistente: "Mucho gusto Jorge! ¿En qué puedo ayudarte?"
           │
           ▼
    🔍 StateReducer (background)
           │
           ├─> Extrae: nombre = "Jorge"
           ├─> Extrae: ciudad = "San José"  
           ├─> Determina scope: user:ciudad (persiste)
           │
           ▼
    State automáticamente actualizado:
    {
      "nombre": "Jorge",
      "user:ciudad": "San José"
    }
```

## 🎯 Componentes

### 1. **StateReducer** (`Agent/state_reducer.py`)

Clase principal que:
- Recibe cada turno de conversación
- Llama al LLM para extraer información estructurada
- Determina el scope apropiado (session/user/app)
- Retorna updates para aplicar al state

```python
class StateReducer:
    def reduce_state(
        self,
        current_state: State,
        user_message: str,
        assistant_message: str
    ) -> Dict[str, Any]:
        """Extrae y retorna state updates"""
```

### 2. **Extraction Prompt**

El LLM recibe:
- Mensaje del usuario
- Respuesta del asistente
- State actual (para contexto)

Y retorna JSON estructurado:
```json
{
  "session": {"tema_actual": "política"},
  "user": {"nombre": "Jorge", "ciudad": "San José"},
  "app": {}
}
```

### 3. **Integration Point**

Se ejecuta automáticamente en `programmatic_agent.py` después de cada turno:

```python
# Después de generar la respuesta...
updates = self.state_reducer.reduce_state(
    self.state,
    user_message=query,
    assistant_message=response
)

if updates:
    self.state_reducer.apply_updates(self.state, updates)
    # State se persiste automáticamente después
```

## 📊 Comparación: Before vs After

### ❌ Antes (Manual)

Usuario tenía que decir explícitamente:
```
"Escribe código para guardar mi nombre 'Jorge' en el estado"
```

El LLM generaba:
```python
state["nombre"] = "Jorge"
```

### ✅ Ahora (Automático)

Usuario habla naturalmente:
```
"Hola, me llamo Jorge"
```

El sistema **automáticamente**:
1. Detecta la información
2. Extrae "nombre = Jorge"
3. Guarda en el state
4. Todo transparente para el usuario

## 🔧 Scopes Automáticos

El reducer determina el scope apropiado:

| Información | Scope | Ejemplo |
|-------------|-------|---------|
| Nombre | session | `nombre: "Jorge"` |
| Idioma preferido | user | `user:idioma: "español"` |
| Ciudad | user | `user:ciudad: "San José"` |
| Intereses | user | `user:intereses: ["política", "tecnología"]` |
| Contador de búsquedas | app | `app:total_searches: 42` |
| Tema actual | session | `tema_actual: "política"` |

## 🚀 Beneficios

1. **UX Natural**: Usuario habla normalmente, no necesita comandos especiales
2. **Transparente**: El enriquecimiento sucede en background
3. **Inteligente**: El LLM decide qué guardar y dónde
4. **Persistente**: user: y app: scopes persisten automáticamente
5. **Escalable**: Fácil agregar nuevos tipos de información

## 🎓 Inspiración: LangGraph

Este patrón está inspirado en **LangGraph reducers**:

```python
# LangGraph style
class State(TypedDict):
    messages: Annotated[list, add_messages_reducer]
    user_info: Annotated[dict, merge_dicts_reducer]
```

Nosotros adaptamos esto a:
- Usar un LLM para extracción inteligente
- Soportar múltiples scopes (session/user/app)
- Integrar con el sistema de persistencia SQLite

## 📝 Logs de Ejemplo

```
🔍 Auto-enriching state...
📝 State updates: {"nombre": "Jorge", "user:ciudad": "San José"}
  ✏️  State [session]: nombre = Jorge
  ✏️  State [user]: user:ciudad = San José
✅ Auto-extracted 2 items from conversation

==================================================
💾 Saving State...
  📦 Session: nombre = Jorge
  👤 User: user:ciudad = San José
  ✅ Saved 1 session keys
  ✅ Saved 1 user keys
==================================================
```

## 🔍 Debugging

Si el reducer no está extrayendo información correctamente:

1. **Check los logs**: Busca `🔍 Auto-enriching state...`
2. **Revisa el extraction prompt** en `state_reducer.py`
3. **Ajusta el modelo**: Cambia `extraction_model` a uno más potente
4. **Mejora los ejemplos** en el prompt de extracción

## 🎯 Próximos Pasos

- [ ] Agregar más tipos de información (email, teléfono, etc.)
- [ ] Mejorar detección de preferencias implícitas
- [ ] Agregar confidence scores
- [ ] Permitir que el usuario corrija información auto-extraída
- [ ] Dashboard para visualizar estado enriquecido

## 📚 Referencias

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Reducers in LangGraph](https://langchain-ai.github.io/langgraph/concepts/#reducers)
- Google ADK State Management
