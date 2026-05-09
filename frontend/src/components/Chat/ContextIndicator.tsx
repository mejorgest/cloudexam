import { useState } from 'react';
import { useAppStore } from '../../store/appStore';

export function ContextIndicator() {
    const { contextInfo } = useAppStore();
    const [showPopup, setShowPopup] = useState(false);

    if (!contextInfo) return null;

    // Calculate percentage
    const tokenPercent = Math.min(100, Math.round((contextInfo.token_count / contextInfo.max_tokens) * 100));
    const msgPercent = Math.min(100, Math.round((contextInfo.non_system_messages / contextInfo.max_messages) * 100));
    const percent = Math.max(tokenPercent, msgPercent);

    // Determine status
    const isCompacting = contextInfo.needs_compaction;
    const fillClass = percent >= 90 ? 'high' : percent >= 70 ? 'medium' : 'low';
    const textClass = percent >= 90 ? 'danger' : percent >= 70 ? 'warn' : '';

    const getText = () => {
        if (isCompacting) return '⟳ Compactando...';
        if (percent >= 90) return `${percent}% ⚠️`;
        return `${percent}%`;
    };

    return (
        <div
            className="context-indicator"
            onClick={() => setShowPopup(!showPopup)}
            style={{ position: 'relative' }}
        >
            <div className="context-bar">
                <div
                    className={`context-fill ${fillClass} ${isCompacting ? 'compacting' : ''}`}
                    style={{ width: `${percent}%` }}
                />
            </div>
            <span className={`context-text ${textClass} ${isCompacting ? 'compacting' : ''}`}>
                {getText()}
            </span>

            {/* Popup */}
            {showPopup && (
                <div className="context-popup">
                    <h4>📊 Contexto LLM</h4>
                    <div className="context-popup-row">
                        <span className="context-popup-label">Mensajes:</span>
                        <span className="context-popup-value">
                            {contextInfo.non_system_messages} / {contextInfo.max_messages}
                        </span>
                    </div>
                    <div className="context-popup-row">
                        <span className="context-popup-label">Tokens:</span>
                        <span className="context-popup-value">
                            {(contextInfo.token_count / 1000).toFixed(1)}K / {(contextInfo.max_tokens / 1000).toFixed(0)}K
                        </span>
                    </div>
                    <div className="context-popup-row">
                        <span className="context-popup-label">Compactaciones:</span>
                        <span className="context-popup-value">
                            {contextInfo.compaction_count || 0}
                        </span>
                    </div>
                    {percent >= 85 && (
                        <div style={{
                            marginTop: 12,
                            padding: '8px',
                            background: 'rgba(248, 81, 73, 0.1)',
                            borderRadius: 6,
                            fontSize: 11,
                            color: 'var(--accent-yellow)'
                        }}>
                            ⚠️ El contexto se compactará pronto
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
