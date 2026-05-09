import { useEffect, useState, useCallback } from 'react';

interface KeyMeta {
    name: string;
    label: string;
    required: boolean;
    secret: boolean;
    help: string;
    configured: boolean;
    preview: string;
}

interface ConfigStatus {
    keys: KeyMeta[];
    needs_setup: boolean;
    missing_required: string[];
}

async function fetchStatus(): Promise<ConfigStatus> {
    const r = await fetch('/api/config/status');
    if (!r.ok) throw new Error(`status ${r.status}`);
    return r.json();
}

async function saveKeys(updates: Record<string, string>): Promise<ConfigStatus & { applied: string[]; rejected: { key: string; reason: string }[]; agent: string }> {
    const r = await fetch('/api/config/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: updates }),
    });
    if (!r.ok) throw new Error(`save ${r.status}`);
    return r.json();
}

interface Props {
    /** When true, render as a full-screen blocking modal (initial setup). */
    blocking: boolean;
    /** Called after a successful save (used by the settings dialog to close). */
    onClose?: () => void;
    /** Called once we've confirmed setup is complete (used by App to switch to main UI). */
    onConfigured?: () => void;
}

export function ConfigScreen({ blocking, onClose, onConfigured }: Props) {
    const [status, setStatus] = useState<ConfigStatus | null>(null);
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [rejected, setRejected] = useState<{ key: string; reason: string }[]>([]);

    const load = useCallback(async () => {
        try {
            const s = await fetchStatus();
            setStatus(s);
        } catch (e) {
            setError(String(e));
        }
    }, []);

    useEffect(() => {
        load();
    }, [load]);

    const handleSave = async () => {
        setSaving(true);
        setError(null);
        setRejected([]);
        try {
            const res = await saveKeys(drafts);
            setStatus(res);
            setRejected(res.rejected || []);
            setDrafts({});
            if (!res.needs_setup) {
                if (blocking) onConfigured?.();
                else onClose?.();
            }
        } catch (e) {
            setError(String(e));
        } finally {
            setSaving(false);
        }
    };

    if (!status) {
        return (
            <div className="config-screen">
                <div className="config-card"><p>Cargando configuración…</p></div>
            </div>
        );
    }

    return (
        <div className={`config-screen ${blocking ? 'blocking' : ''}`}>
            <div className="config-card">
                <div className="config-header">
                    <h2>{blocking ? '🔧 Configuración inicial' : '⚙️ Configuración'}</h2>
                    {!blocking && (
                        <button className="config-close" onClick={onClose} aria-label="Cerrar">×</button>
                    )}
                </div>

                {blocking && (
                    <p className="config-intro">
                        Antes de empezar, configura las API keys. Se guardan en disco
                        (<code>data/secrets.json</code>) y persisten entre reinicios.
                    </p>
                )}

                {error && <div className="config-error">{error}</div>}
                {rejected.length > 0 && (
                    <div className="config-error">
                        {rejected.map(r => (
                            <div key={r.key}><b>{r.key}</b>: {r.reason}</div>
                        ))}
                    </div>
                )}

                <div className="config-fields">
                    {status.keys.map(k => {
                        const value = drafts[k.name] ?? '';
                        const showPlaceholder = k.configured && !(k.name in drafts);
                        return (
                            <div className="config-field" key={k.name}>
                                <label>
                                    <span className="config-label">
                                        {k.label}
                                        {k.required && <span className="config-required" title="Requerido">*</span>}
                                        {k.configured && <span className="config-status-dot" title="Configurada">●</span>}
                                    </span>
                                    {k.help && <span className="config-help">{k.help}</span>}
                                    <input
                                        type={k.secret ? 'password' : 'text'}
                                        value={value}
                                        placeholder={showPlaceholder ? `(actual: ${k.preview}) — escribe para reemplazar` : 'sin configurar'}
                                        onChange={(e) => setDrafts(d => ({ ...d, [k.name]: e.target.value }))}
                                        autoComplete="off"
                                        spellCheck={false}
                                    />
                                </label>
                            </div>
                        );
                    })}
                </div>

                <div className="config-actions">
                    {!blocking && (
                        <button className="config-btn ghost" onClick={onClose} disabled={saving}>Cancelar</button>
                    )}
                    <button
                        className="config-btn primary"
                        onClick={handleSave}
                        disabled={saving || Object.keys(drafts).length === 0}
                    >
                        {saving ? 'Guardando…' : 'Guardar'}
                    </button>
                </div>

                {status.needs_setup && (
                    <p className="config-warning">
                        Faltan keys requeridas: <b>{status.missing_required.join(', ')}</b>
                    </p>
                )}
            </div>
        </div>
    );
}
