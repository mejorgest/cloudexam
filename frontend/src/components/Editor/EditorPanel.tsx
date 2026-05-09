import { useState, useEffect, useCallback, useRef } from 'react';
import { useAppStore } from '../../store/appStore';
import { readFile, writeFile } from '../../services/api';
import { ExamViewer } from '../ExamViewer';
import { ImageUpload } from '../ImageUpload';
import { X, Edit, Save, CornerUpLeft } from 'lucide-react';
import type { ExamQuestion } from '../../types';

export function EditorPanel() {
    const {
        selectedKey,
        openTabs,
        closeTab,
        switchToTab,
        isEditMode,
        setIsEditMode,
        tabScrollPositions,
    } = useAppStore();

    const [content, setContent] = useState<string>('');
    const [editContent, setEditContent] = useState<string>('');
    const [isExam, setIsExam] = useState(false);
    const [examData, setExamData] = useState<ExamQuestion[] | null>(null);
    const [originalExamData, setOriginalExamData] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const editorRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    // Track if content is already loaded to prevent unnecessary reloads
    const contentLoadedRef = useRef<string | null>(null);
    const prevAnalyzingRef = useRef<number | null>(null);


    // When analysis finishes (currentAnalyzingIndex goes null),
    // merge the saved justification into examData IN MEMORY (no page reload!)
    const { currentAnalyzingIndex, streamingJustification, lastAnalyzedIndex } = useAppStore();
    useEffect(() => {
        if (prevAnalyzingRef.current !== null && currentAnalyzingIndex === null) {
            // Analysis just finished - merge justification without reloading
            const analyzedIdx = lastAnalyzedIndex;
            if (analyzedIdx !== null && examData && streamingJustification) {
                // Update the specific question's justification in memory
                const updatedQuestions = [...examData];
                if (updatedQuestions[analyzedIdx]) {
                    updatedQuestions[analyzedIdx] = { ...updatedQuestions[analyzedIdx], justificacion: streamingJustification };
                    setExamData(updatedQuestions);

                    // Also sync `content` so edit mode shows current data
                    let syncedContent: string;
                    if (originalExamData) {
                        try {
                            const original = JSON.parse(originalExamData);
                            if (!Array.isArray(original) && original.preguntas) {
                                syncedContent = JSON.stringify({
                                    ...original,
                                    preguntas: updatedQuestions,
                                    total_preguntas: updatedQuestions.length,
                                }, null, 2);
                            } else {
                                syncedContent = JSON.stringify(updatedQuestions, null, 2);
                            }
                        } catch {
                            syncedContent = JSON.stringify(updatedQuestions, null, 2);
                        }
                    } else {
                        syncedContent = JSON.stringify(updatedQuestions, null, 2);
                    }
                    setContent(syncedContent);
                    setEditContent(syncedContent);
                }
                console.log('✅ [EditorPanel] Merged justification for Q', analyzedIdx + 1, 'in-memory + synced content');
            }
        }
        prevAnalyzingRef.current = currentAnalyzingIndex;
    }, [currentAnalyzingIndex, lastAnalyzedIndex, streamingJustification, examData, originalExamData]);

    // Load content when selected key changes (NOT when state changes for files)
    useEffect(() => {
        if (!selectedKey) {
            setContent('');
            setIsExam(false);
            setExamData(null);
            contentLoadedRef.current = null;
            return;
        }

        // Skip special keys
        if (selectedKey === '__images__') return;

        // For file: keys, don't reload if we already loaded this file
        // This prevents overwriting user edits when the file is saved by streaming
        if (selectedKey.startsWith('file:') && contentLoadedRef.current === selectedKey) {
            return;
        }

        const loadContent = async () => {
            setLoading(true);
            try {
                let rawContent = '';

                if (selectedKey.startsWith('file:')) {
                    const filename = selectedKey.replace('file:', '');
                    const data = await readFile(filename);
                    rawContent = data.content || '';
                }

                setContent(rawContent);
                setEditContent(rawContent);
                contentLoadedRef.current = selectedKey;

                // Check if exam JSON
                if (selectedKey.endsWith('.json') || selectedKey.includes('examen')) {
                    try {
                        const parsed = JSON.parse(rawContent);
                        let questions: ExamQuestion[];

                        if (Array.isArray(parsed)) {
                            questions = parsed;
                        } else if (parsed && Array.isArray(parsed.preguntas)) {
                            questions = parsed.preguntas;
                        } else {
                            questions = [];
                        }

                        if (questions.length > 0 && questions[0].pregunta !== undefined) {
                            setIsExam(true);
                            setExamData(questions);
                            setOriginalExamData(rawContent);
                        } else {
                            setIsExam(false);
                            setExamData(null);
                        }
                    } catch {
                        setIsExam(false);
                        setExamData(null);
                    }
                } else {
                    setIsExam(false);
                    setExamData(null);
                }

                // Restore scroll position
                setTimeout(() => {
                    const savedPosition = tabScrollPositions[selectedKey];
                    if (savedPosition) {
                        if (savedPosition.type === 'exam') {
                            const examContainer = document.querySelector('.exam-container');
                            if (examContainer) examContainer.scrollTop = savedPosition.scrollTop;
                        } else if (editorRef.current) {
                            editorRef.current.scrollTop = savedPosition.scrollTop;
                        }
                    }
                }, 100);
            } catch (error) {
                console.error('Error loading content:', error);
                setContent('Error loading content');
            } finally {
                setLoading(false);
            }
        };

        loadContent();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedKey]); // Only reload when tab changes, NOT when scroll positions change

    // Toggle edit mode
    const handleToggleEdit = useCallback(() => {
        if (isEditMode) {
            // Cancel edit - revert to current content
            setEditContent(content);
        } else {
            // Entering edit mode - serialize current examData (with in-memory justifications)
            if (isExam && examData) {
                let currentContent: string;
                if (originalExamData) {
                    try {
                        const original = JSON.parse(originalExamData);
                        if (!Array.isArray(original) && original.preguntas) {
                            currentContent = JSON.stringify({
                                ...original,
                                preguntas: examData,
                                total_preguntas: examData.length,
                            }, null, 2);
                        } else {
                            currentContent = JSON.stringify(examData, null, 2);
                        }
                    } catch {
                        currentContent = JSON.stringify(examData, null, 2);
                    }
                } else {
                    currentContent = JSON.stringify(examData, null, 2);
                }
                setEditContent(currentContent);
                // Also sync content so cancel returns to current state
                setContent(currentContent);
            }
        }
        setIsEditMode(!isEditMode);
    }, [isEditMode, content, setIsEditMode, isExam, examData, originalExamData]);

    // Save content
    const handleSave = useCallback(async () => {
        if (!selectedKey || !selectedKey.startsWith('file:')) return;

        try {
            const filename = selectedKey.replace('file:', '');
            await writeFile(filename, editContent);

            setContent(editContent);
            setIsEditMode(false);

            // Re-check if exam
            try {
                const parsed = JSON.parse(editContent);
                if (Array.isArray(parsed) && parsed[0]?.pregunta) {
                    setIsExam(true);
                    setExamData(parsed);
                }
            } catch {
                // Not JSON, fine
            }
        } catch (error) {
            console.error('Error saving:', error);
        }
    }, [selectedKey, editContent, setIsEditMode]);

    // Get icon for tab
    const getTabIcon = (tab: { key: string; type: string }) => {
        if (tab.key === '__images__') return '🖼️';
        if (tab.key.includes('examen') || tab.key.endsWith('.json')) return '📋';
        if (tab.type === 'file') return '📝';
        return '📄';
    };

    // Render code view with line numbers
    const renderCodeView = () => {
        const lines = content.split('\n');
        return (
            <div className="code-view">
                {lines.map((line, i) => (
                    <div key={i} className="code-line">
                        <span className="line-number">{i + 1}</span>
                        <span className="line-content">{line || ' '}</span>
                    </div>
                ))}
            </div>
        );
    };

    // Render empty state
    if (!selectedKey) {
        return (
            <div className="editor-panel">
                <div className="editor-tabs">
                    <div className="editor-tab active">
                        <span>👋</span> Welcome
                    </div>
                </div>
                <div className="editor-content">
                    <div className="empty-state">
                        <div className="icon">🤖</div>
                        <h3>React Agent IDE</h3>
                        <p>Selecciona un archivo del explorador</p>
                    </div>
                </div>
                <StatusBar />
            </div>
        );
    }

    return (
        <div className="editor-panel">
            {/* Tabs */}
            <div className="editor-tabs">
                {openTabs.map(tab => (
                    <div
                        key={tab.key}
                        className={`editor-tab ${selectedKey === tab.key ? 'active' : ''}`}
                        onClick={() => switchToTab(tab.key)}
                    >
                        <span className="editor-tab-icon">{getTabIcon(tab)}</span>
                        <span className="text-ellipsis" style={{ maxWidth: 120 }}>{tab.name}</span>
                        <button
                            className="editor-tab-close"
                            onClick={(e) => {
                                e.stopPropagation();
                                closeTab(tab.key);
                            }}
                        >
                            <X size={14} />
                        </button>
                    </div>
                ))}
            </div>

            {/* Content */}
            <div
                ref={editorRef}
                className={`editor-content ${isEditMode ? 'edit-mode' : ''}`}
                id="editorContent"
            >
                {selectedKey === '__images__' ? (
                    <ImageUpload />
                ) : loading ? (
                    <div className="empty-state">
                        <div className="loading-spinner-modern">
                            <div className="spinner-ring" />
                        </div>
                        <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '12px' }}>Cargando...</p>
                    </div>
                ) : isEditMode ? (
                    <textarea
                        ref={textareaRef}
                        className="code-editor"
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        autoFocus
                    />
                ) : isExam && examData ? (
                    <ExamViewer
                        questions={examData}
                        examKey={selectedKey}
                        originalData={originalExamData}
                        onUpdate={(newQuestions: ExamQuestion[]) => {
                            setExamData(newQuestions);
                        }}
                    />
                ) : (
                    renderCodeView()
                )}
            </div>

            {/* Status Bar */}
            <StatusBar
                isEditMode={isEditMode}
                onToggleEdit={handleToggleEdit}
                onSave={handleSave}
                selectedKey={selectedKey}
            />
        </div>
    );
}

// ============== Status Bar Component ==============
interface StatusBarProps {
    isEditMode?: boolean;
    onToggleEdit?: () => void;
    onSave?: () => void;
    selectedKey?: string | null;
}

function StatusBar({ isEditMode, onToggleEdit, onSave, selectedKey }: StatusBarProps) {
    return (
        <div className="status-bar">
            <div className="status-left">
                {selectedKey && (
                    <span className="status-item">
                        📁 {selectedKey.replace('file:', '')}
                    </span>
                )}
            </div>
            <div className="status-right">
                {selectedKey && onToggleEdit && (
                    <>
                        {isEditMode ? (
                            <>
                                <button className="status-btn" onClick={onToggleEdit}>
                                    <CornerUpLeft size={14} /> Cancelar
                                </button>
                                <button className="status-btn success" onClick={onSave}>
                                    <Save size={14} /> Guardar
                                </button>
                            </>
                        ) : (
                            <button className="status-btn primary" onClick={onToggleEdit}>
                                <Edit size={14} /> Editar
                            </button>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
