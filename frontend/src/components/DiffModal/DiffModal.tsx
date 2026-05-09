import { useState, useEffect, useCallback } from 'react';
import { fetchDiff, restoreCheckpoint } from '../../services/api';
import { useAppStore } from '../../store/appStore';
import { X, ChevronUp, ChevronDown, Check, RotateCcw } from 'lucide-react';
import type { DiffLine } from '../../types';

interface DiffModalProps {
    isOpen: boolean;
    stateKey: string;
    onClose: () => void;
}

export function DiffModal({ isOpen, stateKey, onClose }: DiffModalProps) {
    const { addMessage, removePendingChange } = useAppStore();

    const [diffLines, setDiffLines] = useState<DiffLine[]>([]);
    const [hasChanges, setHasChanges] = useState(false);
    const [previousHash, setPreviousHash] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [changePositions, setChangePositions] = useState<number[]>([]);
    const [currentChangeIndex, setCurrentChangeIndex] = useState(-1);

    // Load diff data
    useEffect(() => {
        if (!isOpen || !stateKey) return;

        const loadDiff = async () => {
            setLoading(true);
            try {
                const data = await fetchDiff(stateKey);

                if (!data.success) {
                    addMessage({
                        id: `system-${Date.now()}`,
                        type: 'system',
                        content: `❌ Error: ${data.error || 'No se pudo cargar el diff'}`,
                        timestamp: new Date(),
                    });
                    onClose();
                    return;
                }

                setDiffLines(data.diff || []);
                setHasChanges(data.has_changes);
                setPreviousHash(data.previous_hash || null);

                // Find change positions for navigation
                const positions: number[] = [];
                data.diff?.forEach((line, i) => {
                    if (line.type !== 'same') {
                        const lastPos = positions[positions.length - 1];
                        if (lastPos === undefined || i - lastPos > 2) {
                            positions.push(i);
                        }
                    }
                });
                setChangePositions(positions);
                setCurrentChangeIndex(positions.length > 0 ? 0 : -1);

            } catch (error) {
                console.error('Error loading diff:', error);
            } finally {
                setLoading(false);
            }
        };

        loadDiff();
    }, [isOpen, stateKey, addMessage, onClose]);

    // Navigate to change
    const navigateChange = useCallback((direction: number) => {
        if (changePositions.length === 0) return;

        let newIndex = currentChangeIndex + direction;
        if (newIndex < 0) newIndex = changePositions.length - 1;
        if (newIndex >= changePositions.length) newIndex = 0;

        setCurrentChangeIndex(newIndex);

        // Scroll to change
        const newPanel = document.getElementById('diffNewContent');
        const oldPanel = document.getElementById('diffOldContent');
        const lineIndex = changePositions[newIndex];

        const scrollTo = lineIndex * 24 - 100; // Approximate line height
        if (newPanel) newPanel.scrollTop = scrollTo;
        if (oldPanel) oldPanel.scrollTop = scrollTo;
    }, [changePositions, currentChangeIndex]);

    // Handle keyboard navigation
    useEffect(() => {
        if (!isOpen) return;

        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                onClose();
            } else if (e.key === 'ArrowUp' || e.key === 'k') {
                navigateChange(-1);
                e.preventDefault();
            } else if (e.key === 'ArrowDown' || e.key === 'j') {
                navigateChange(1);
                e.preventDefault();
            }
        };

        document.addEventListener('keydown', handleKeyDown);
        return () => document.removeEventListener('keydown', handleKeyDown);
    }, [isOpen, onClose, navigateChange]);

    // Approve changes
    const handleApprove = () => {
        addMessage({
            id: `system-${Date.now()}`,
            type: 'system',
            content: '✅ Versión actual aprobada',
            timestamp: new Date(),
        });
        removePendingChange(stateKey);
        onClose();
    };

    // Revert to previous
    const handleRevert = async () => {
        if (!previousHash) {
            addMessage({
                id: `system-${Date.now()}`,
                type: 'system',
                content: '❌ No hay versión anterior para revertir',
                timestamp: new Date(),
            });
            return;
        }

        try {
            const result = await restoreCheckpoint(previousHash);

            if (result.success) {
                addMessage({
                    id: `system-${Date.now()}`,
                    type: 'system',
                    content: '🔄 Revertido a versión anterior',
                    timestamp: new Date(),
                });
                removePendingChange(stateKey);
                onClose();
            } else {
                addMessage({
                    id: `system-${Date.now()}`,
                    type: 'system',
                    content: `❌ Error: ${result.error || 'No se pudo revertir'}`,
                    timestamp: new Date(),
                });
            }
        } catch (error) {
            console.error('Error reverting:', error);
        }
    };

    // Render diff line
    const renderDiffLine = (line: DiffLine, index: number, side: 'old' | 'new') => {
        const content = side === 'old' ? line.old : line.new;
        const lineNum = line.type === 'same' || line.type === 'change'
            ? index + 1
            : (side === 'old' && line.type === 'remove') || (side === 'new' && line.type === 'add')
                ? index + 1
                : '';

        const className = `diff-line ${line.type}`;
        const isHighlighted = changePositions[currentChangeIndex] === index;

        return (
            <div key={index} className={`${className} ${isHighlighted ? 'highlight' : ''}`}>
                <div className="diff-line-number">{lineNum}</div>
                <div className="diff-line-content">{content || ' '}</div>
            </div>
        );
    };

    // Sync scroll between panels
    const handleScroll = (e: React.UIEvent<HTMLDivElement>, targetId: string) => {
        const target = document.getElementById(targetId);
        if (target) {
            target.scrollTop = (e.target as HTMLDivElement).scrollTop;
        }
    };

    if (!isOpen) return null;

    return (
        <div className={`modal-overlay ${isOpen ? 'active' : ''}`} onClick={onClose}>
            <div className="modal-content" onClick={e => e.stopPropagation()}>
                {/* Header */}
                <div className="modal-header">
                    <h3 className="modal-title">Comparar: {stateKey}</h3>
                    <div className="modal-nav">
                        <button
                            onClick={() => navigateChange(-1)}
                            disabled={changePositions.length <= 1}
                        >
                            <ChevronUp size={16} />
                        </button>
                        <span className={`diff-counter ${changePositions.length === 0 ? 'no-changes' : ''}`}>
                            {changePositions.length === 0
                                ? 'Sin cambios'
                                : `${currentChangeIndex + 1} / ${changePositions.length}`}
                        </span>
                        <button
                            onClick={() => navigateChange(1)}
                            disabled={changePositions.length <= 1}
                        >
                            <ChevronDown size={16} />
                        </button>
                    </div>
                    <button className="modal-close" onClick={onClose}>
                        <X size={24} />
                    </button>
                </div>

                {/* Diff Content */}
                <div className="diff-container">
                    {loading ? (
                        <div className="empty-state">
                            <div className="icon">⏳</div>
                            <p>Cargando diff...</p>
                        </div>
                    ) : !hasChanges ? (
                        <div className="diff-no-changes">
                            <div className="icon">✓</div>
                            <h4>Sin cambios</h4>
                            <p>El documento no tiene cambios desde la última versión guardada.</p>
                        </div>
                    ) : (
                        <>
                            {/* Old Panel */}
                            <div className="diff-panel">
                                <div className="diff-panel-header">Anterior</div>
                                <div
                                    id="diffOldContent"
                                    className="diff-panel-content"
                                    onScroll={(e) => handleScroll(e, 'diffNewContent')}
                                >
                                    {diffLines.map((line, i) => renderDiffLine(line, i, 'old'))}
                                </div>
                            </div>

                            {/* New Panel */}
                            <div className="diff-panel">
                                <div className="diff-panel-header">Actual</div>
                                <div
                                    id="diffNewContent"
                                    className="diff-panel-content"
                                    onScroll={(e) => handleScroll(e, 'diffOldContent')}
                                >
                                    {diffLines.map((line, i) => renderDiffLine(line, i, 'new'))}
                                </div>
                            </div>
                        </>
                    )}
                </div>

                {/* Actions */}
                <div className="modal-actions">
                    <button className="modal-btn secondary" onClick={onClose}>
                        Cerrar
                    </button>
                    <button
                        className="modal-btn danger"
                        onClick={handleRevert}
                        disabled={!previousHash || !hasChanges}
                    >
                        <RotateCcw size={16} /> Revertir
                    </button>
                    <button className="modal-btn primary" onClick={handleApprove}>
                        <Check size={16} /> Aprobar
                    </button>
                </div>
            </div>
        </div>
    );
}
