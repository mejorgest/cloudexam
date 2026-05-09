import { useState, useCallback, useRef, memo } from 'react';
import { useAppStore } from '../../store/appStore';
import { deleteFile, readFile, extractExamFromPdf } from '../../services/api';
import { ChevronDown, Plus, FolderOpen, FileText, Loader2 } from 'lucide-react';

export function Sidebar() {
    const {
        files,
        selectedKey,
        openTab,
        setSelectedKey,
        addAttachedFile,
        attachedFiles,
        toggleDebug,
        debugOpen,
        a2uiImagesEnabled,
        toggleA2UIImages,
        setSidebarOpen,
    } = useAppStore();

    const [filesOpen, setFilesOpen] = useState(true);

    // Exam extraction state
    const [examExtracting, setExamExtracting] = useState(false);
    const [examResult, setExamResult] = useState<{ success: boolean; message: string } | null>(null);
    const examFileRef = useRef<HTMLInputElement>(null);

    // Auto-close sidebar drawer on mobile after selecting something.
    const closeOnMobile = useCallback(() => {
        if (window.innerWidth < 768) setSidebarOpen(false);
    }, [setSidebarOpen]);

    // Open a file in a tab.
    const handleSelectFile = useCallback((name: string) => {
        const key = `file:${name}`;
        openTab({ key, type: 'file', name });
        setSelectedKey(key);
        closeOnMobile();
    }, [openTab, setSelectedKey, closeOnMobile]);

    // Attach a file's content to the chat context.
    const handleAttachFile = useCallback(async (name: string) => {
        let content = '';
        try {
            const data = await readFile(name);
            content = data.content || '';
        } catch (e) {
            console.error('Error reading file for attach:', e);
            return;
        }

        const isAlreadyAttached = attachedFiles.some(
            f => f.type === 'file' && f.name === name
        );
        if (!isAlreadyAttached) {
            addAttachedFile({ type: 'file', name, content });
        }
    }, [attachedFiles, addAttachedFile]);

    const handleDeleteFile = useCallback(async (name: string, e: React.MouseEvent) => {
        e.stopPropagation();
        const ok = window.confirm(`¿Eliminar archivo "${name}"?`);
        if (!ok) return;
        try {
            await deleteFile(name);
        } catch (error) {
            console.error('Delete error:', error);
        }
    }, []);

    const handleCreateFile = useCallback(() => {
        const name = prompt('Nombre del nuevo archivo (ej: notes.txt):');
        if (!name) return;
        handleSelectFile(name);
    }, [handleSelectFile]);

    // Extract exam from PDF
    const handleExtractExam = useCallback(async (file: File) => {
        const baseName = file.name.replace(/\.pdf$/i, '').replace(/[^a-zA-Z0-9_áéíóúñÁÉÍÓÚÑ\s]/g, '').trim();
        const outputName = prompt('Nombre para el examen extraído:', `examen_${baseName}`) || `examen_${baseName}`;

        setExamExtracting(true);
        setExamResult(null);

        try {
            const result = await extractExamFromPdf(file, outputName);
            if (result.success && result.filename) {
                setExamResult({
                    success: true,
                    message: `✅ ${result.total_questions} preguntas extraídas → ${result.filename}`
                });
                setTimeout(() => {
                    handleSelectFile(result.filename!);
                }, 500);
            } else {
                setExamResult({ success: false, message: `❌ ${result.error || 'Error desconocido'}` });
            }
        } catch (error) {
            setExamResult({ success: false, message: `❌ ${error instanceof Error ? error.message : 'Error'}` });
        } finally {
            setExamExtracting(false);
            if (examFileRef.current) examFileRef.current.value = '';
            setTimeout(() => setExamResult(null), 8000);
        }
    }, [handleSelectFile]);

    return (
        <aside className="sidebar">
            <div className="sidebar-header">
                <div className="sidebar-title">
                    <FolderOpen size={16} />
                    Explorer
                </div>
                <div className="sidebar-actions">
                    <button className="sidebar-btn" onClick={handleCreateFile} title="Nuevo archivo">
                        <Plus size={14} /> Archivo
                    </button>
                </div>
            </div>

            <div className="sidebar-content">
                {/* Workspace Files Section */}
                <div className="section">
                    <div
                        className={`section-header ${!filesOpen ? 'collapsed' : ''}`}
                        onClick={() => setFilesOpen(!filesOpen)}
                    >
                        <ChevronDown size={12} className="chevron" />
                        <span>📁 ARCHIVOS DE EXAMEN</span>
                        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-muted)' }}>
                            {files.length}
                        </span>
                    </div>
                    <div className={`section-content ${!filesOpen ? 'collapsed' : ''}`}>
                        {files.length === 0 ? (
                            <div className="tree-item" style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                                Sin archivos
                            </div>
                        ) : (
                            files.map(file => (
                                <TreeItem
                                    key={file.name}
                                    name={file.name}
                                    icon={file.name.endsWith('.json') ? '📋' : '📝'}
                                    selected={selectedKey === `file:${file.name}`}
                                    isAttached={attachedFiles.some(f => f.type === 'file' && f.name === file.name)}
                                    onSelect={() => handleSelectFile(file.name)}
                                    onAttach={() => handleAttachFile(file.name)}
                                    onDelete={(e) => handleDeleteFile(file.name, e)}
                                />
                            ))
                        )}
                    </div>
                </div>

                {/* Exam Extraction Section */}
                <div className="section">
                    <div className="section-header">
                        <FileText size={12} />
                        <span>📝 PROCESAR EXAMEN PDF</span>
                    </div>
                    <div className="section-content">
                        <label
                            className="tree-item exam-extract-btn"
                            style={{
                                cursor: examExtracting ? 'wait' : 'pointer',
                                opacity: examExtracting ? 0.7 : 1,
                                display: 'flex',
                                alignItems: 'center',
                                gap: '6px',
                                padding: '6px 8px',
                                borderRadius: '4px',
                                background: 'rgba(139, 92, 246, 0.08)',
                                border: '1px dashed rgba(139, 92, 246, 0.3)',
                                marginBottom: '4px',
                                transition: 'all 0.2s',
                            }}
                        >
                            {examExtracting ? (
                                <Loader2 size={14} className="spin-icon" />
                            ) : (
                                <FileText size={14} style={{ color: 'var(--accent-purple, #8b5cf6)' }} />
                            )}
                            <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                                {examExtracting ? 'Extrayendo preguntas...' : 'Subir PDF de examen...'}
                            </span>
                            <input
                                ref={examFileRef}
                                type="file"
                                accept=".pdf"
                                style={{ display: 'none' }}
                                disabled={examExtracting}
                                onChange={(e) => {
                                    const file = e.target.files?.[0];
                                    if (file) handleExtractExam(file);
                                }}
                            />
                        </label>
                        {examResult && (
                            <div style={{
                                fontSize: '11px',
                                padding: '4px 8px',
                                borderRadius: '4px',
                                background: examResult.success ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                                color: examResult.success ? '#22c55e' : '#ef4444',
                                marginBottom: '4px',
                            }}>
                                {examResult.message}
                            </div>
                        )}
                        <div style={{ fontSize: '10px', color: 'var(--text-muted)', padding: '0 8px' }}>
                            Usa Gemini AI para extraer preguntas de un PDF de examen al formato JSON del visor.
                        </div>
                    </div>
                </div>
                <div className="section">
                    <div
                        className="section-header"
                        onClick={() => {
                            openTab({ key: '__images__', type: 'images', name: 'Imágenes Médicas' });
                            setSelectedKey('__images__');
                        }}
                        style={{ cursor: 'pointer' }}
                    >
                        <span>🖼️ IMÁGENES MÉDICAS</span>
                        <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-muted)' }}>
                            ↗
                        </span>
                    </div>
                    {/* A2UI Image Search Toggle */}
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '6px 12px',
                        fontSize: '11px',
                        color: 'var(--text-secondary)',
                    }}>
                        <span>🔍 Búsqueda A2UI con LLM</span>
                        <button
                            onClick={(e) => { e.stopPropagation(); toggleA2UIImages(); }}
                            style={{
                                width: '36px',
                                height: '20px',
                                borderRadius: '10px',
                                border: 'none',
                                cursor: 'pointer',
                                position: 'relative',
                                background: a2uiImagesEnabled
                                    ? 'linear-gradient(135deg, #8b5cf6, #6d28d9)'
                                    : 'rgba(255,255,255,0.1)',
                                transition: 'background 0.2s',
                                padding: 0,
                            }}
                            title={a2uiImagesEnabled ? 'Desactivar búsqueda de imágenes A2UI' : 'Activar búsqueda de imágenes A2UI'}
                        >
                            <div style={{
                                width: '16px',
                                height: '16px',
                                borderRadius: '50%',
                                background: '#fff',
                                position: 'absolute',
                                top: '2px',
                                left: a2uiImagesEnabled ? '18px' : '2px',
                                transition: 'left 0.2s',
                                boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
                            }} />
                        </button>
                    </div>
                </div>

                {/* Debug Log Toggle */}
                <div className="section">
                    <div
                        className="section-header"
                        onClick={toggleDebug}
                        style={{ cursor: 'pointer' }}
                    >
                        <span>🐛 DEBUG LOG</span>
                        <span style={{ marginLeft: 'auto', fontSize: 10 }}>
                            {debugOpen ? '▲' : '▼'}
                        </span>
                    </div>
                </div>
            </div>
        </aside>
    );
}

// ============== Tree Item Component ==============
interface TreeItemProps {
    name: string;
    icon: string;
    selected: boolean;
    isAttached: boolean;
    onSelect: () => void;
    onAttach: () => void;
    onDelete: (e: React.MouseEvent) => void;
}

const TreeItem = memo(function TreeItem({ name, icon, selected, isAttached, onSelect, onAttach, onDelete }: TreeItemProps) {
    return (
        <div
            className={`tree-item ${selected ? 'selected' : ''}`}
            onClick={onSelect}
        >
            <span className="tree-item-icon">{icon}</span>
            <span className="tree-item-name">{name}</span>
            <div className="tree-item-actions">
                <button
                    className="tree-item-btn"
                    onClick={(e) => { e.stopPropagation(); onAttach(); }}
                    title={isAttached ? 'Ya adjunto' : 'Adjuntar al contexto'}
                >
                    {isAttached ? '✓' : '📎'}
                </button>
                <button
                    className="tree-item-btn danger"
                    onClick={onDelete}
                    title="Eliminar"
                >
                    🗑️
                </button>
            </div>
        </div>
    );
});
