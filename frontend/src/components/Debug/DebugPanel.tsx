import { useAppStore } from '../../store/appStore';
import { X } from 'lucide-react';

export function DebugPanel() {
    const { changelog, debugOpen, toggleDebug } = useAppStore();

    if (!debugOpen) return null;

    const formatTime = (timestamp: string) => {
        try {
            const date = new Date(timestamp);
            return date.toLocaleTimeString('es-ES', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        } catch {
            return timestamp;
        }
    };

    return (
        <div className={`debug-panel ${debugOpen ? 'open' : ''}`}>
            <div className="debug-header">
                <span className="debug-title">🐛 Debug Log</span>
                <button className="debug-close" onClick={toggleDebug}>
                    <X size={16} />
                </button>
            </div>
            <div className="debug-content">
                {changelog.length === 0 ? (
                    <div style={{
                        textAlign: 'center',
                        padding: 20,
                        color: 'var(--text-muted)'
                    }}>
                        Sin actividad reciente
                    </div>
                ) : (
                    changelog.map((entry, i) => (
                        <div key={i} className="debug-entry">
                            <span className="debug-time">{formatTime(entry.timestamp)}</span>
                            <span className="debug-op">{entry.operation}</span>
                            <span className="debug-target">{entry.target}</span>
                            <span className="debug-details">{entry.details || ''}</span>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}
