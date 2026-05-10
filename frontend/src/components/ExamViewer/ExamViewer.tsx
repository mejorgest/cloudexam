import { useCallback, memo, useMemo, useEffect, useRef, useLayoutEffect } from 'react';
import DOMPurify from 'dompurify';
import { useAppStore } from '../../store/appStore';
import { writeFile } from '../../services/api';
import type { ExamQuestion, ExamOption } from '../../types';
import { Trash2, Plus, FileText, Brain, Save } from 'lucide-react';
import { A2UIImageGallery } from '../A2UIImageGallery';

// Sanitizer config: allow basic inline formatting + color/highlight via style attr.
// Style values are filtered by a regex hook below (DOMPurify v3 dropped ALLOWED_STYLES).
const RICH_TEXT_CONFIG = {
    ALLOWED_TAGS: ['b', 'strong', 'i', 'em', 'u', 'mark', 'span', 'br', 'div', 'font'],
    ALLOWED_ATTR: ['style', 'color'],
};

const SAFE_STYLE_PROPS = new Set(['color', 'background-color', 'font-weight', 'font-style', 'text-decoration']);
const SAFE_STYLE_VALUE = /^(#[0-9a-f]{3,8}|rgba?\([^)]+\)|[a-z][a-z0-9\- ]*)$/i;

DOMPurify.addHook('uponSanitizeAttribute', (_node, data) => {
    if (data.attrName !== 'style') return;
    const cleaned = data.attrValue
        .split(';')
        .map(d => d.trim())
        .filter(Boolean)
        .map(decl => {
            const idx = decl.indexOf(':');
            if (idx < 0) return null;
            const prop = decl.slice(0, idx).trim().toLowerCase();
            const val = decl.slice(idx + 1).trim();
            if (!SAFE_STYLE_PROPS.has(prop)) return null;
            if (!SAFE_STYLE_VALUE.test(val)) return null;
            return `${prop}: ${val}`;
        })
        .filter(Boolean)
        .join('; ');
    data.attrValue = cleaned;
    if (!cleaned) data.keepAttr = false;
});

const sanitizeRich = (html: string): string => String(DOMPurify.sanitize(html, RICH_TEXT_CONFIG));

interface ExamViewerProps {
    questions: ExamQuestion[];
    examKey: string;
    originalData: string | null;
    onUpdate: (questions: ExamQuestion[]) => void;
}

// Color palette for text formatting
const TEXT_COLORS = [
    { name: 'Rojo', color: '#ff6b6b' },
    { name: 'Verde', color: '#51cf66' },
    { name: 'Azul', color: '#339af0' },
    { name: 'Amarillo', color: '#fcc419' },
    { name: 'Naranja', color: '#ff922b' },
    { name: 'Morado', color: '#cc5de8' },
    { name: 'Cyan', color: '#22b8cf' },
    { name: 'Blanco', color: '#ffffff' },
];

const HIGHLIGHT_COLORS = [
    { name: 'Amarillo', color: 'rgba(252, 196, 25, 0.4)' },
    { name: 'Verde', color: 'rgba(81, 207, 102, 0.4)' },
    { name: 'Azul', color: 'rgba(51, 154, 240, 0.4)' },
    { name: 'Rosa', color: 'rgba(255, 107, 107, 0.4)' },
];

export const ExamViewer = memo(function ExamViewer({ questions, examKey, originalData, onUpdate }: ExamViewerProps) {
    const {
        addSnippetRef,
        setCurrentAnalyzingIndex,
        setAnalysisMode,
        setPendingExamAnalysis,
        setChatInputValue,
        triggerSend,
        currentAnalyzingIndex,
        streamingJustification,
        lastAnalyzedIndex,
        a2uiImagesEnabled,
        setIsExamEditing,
    } = useAppStore();

    // Track which field is currently being edited to suppress re-renders
    const editingFieldRef = useRef<string | null>(null);
    // Track edit timeout for debounced editing flag
    const editingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Format toolbar ref - NO REACT STATE to avoid re-renders
    const toolbarRef = useRef<HTMLDivElement | null>(null);

    // Filter out tool code blocks from justification content
    const filterToolCode = useMemo(() => {
        return (content: string): string => {
            if (!content) return '';
            // Remove complete Python code blocks with tool calls
            let result = content.replace(/```python[\s\S]*?```\n*/g, '');
            // Remove incomplete Python code blocks (during streaming)
            result = result.replace(/```python[\s\S]*$/g, '');
            // Remove tool status divs
            result = result.replace(/<div class="tool-status[^"]*">[^<]*<\/div>\n*/g, '');
            // Remove tool indicator divs
            result = result.replace(/<div class="tool-indicator">[^<]*<\/div>\n*/g, '');
            // Clean up extra newlines
            result = result.replace(/\n{3,}/g, '\n\n');
            return result.trim();
        };
    }, []);

    // Track previous analyzing index to detect when it changes
    const prevAnalyzingRef = useRef<number | null>(null);

    // Scroll to question when analysis starts
    useEffect(() => {
        if (currentAnalyzingIndex !== null && currentAnalyzingIndex !== prevAnalyzingRef.current) {
            // Wait a tiny bit for DOM to render the analysis section
            setTimeout(() => {
                const section = document.getElementById(`analysis-section-${currentAnalyzingIndex}`);
                if (section) {
                    section.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }, 100);
        }
        prevAnalyzingRef.current = currentAnalyzingIndex;
    }, [currentAnalyzingIndex]);

    // Create format toolbar once using DOM (no React re-renders!)
    useEffect(() => {
        // Create toolbar element
        const toolbar = document.createElement('div');
        toolbar.className = 'format-toolbar';
        toolbar.id = 'format-toolbar';
        toolbar.style.cssText = 'display: none; position: fixed; z-index: 1000;';

        // Create buttons
        const createBtn = (html: string, title: string, action: () => void) => {
            const btn = document.createElement('button');
            btn.innerHTML = html;
            btn.title = title;
            btn.onmousedown = (e) => e.preventDefault(); // Keep selection
            btn.onclick = action;
            return btn;
        };

        toolbar.appendChild(createBtn('<strong>B</strong>', 'Negrita', () => document.execCommand('bold')));
        toolbar.appendChild(createBtn('<em>I</em>', 'Cursiva', () => document.execCommand('italic')));
        toolbar.appendChild(createBtn('<u>U</u>', 'Subrayado', () => document.execCommand('underline')));

        // Divider
        const divider = document.createElement('span');
        divider.className = 'toolbar-divider';
        toolbar.appendChild(divider);

        // Color picker button
        const colorBtn = document.createElement('button');
        colorBtn.innerHTML = '🎨';
        colorBtn.title = 'Color de texto';
        colorBtn.onmousedown = (e) => e.preventDefault();
        colorBtn.onclick = () => {
            const picker = document.getElementById('color-picker');
            if (picker) picker.style.display = picker.style.display === 'none' ? 'grid' : 'none';
        };
        toolbar.appendChild(colorBtn);

        // Highlight button
        const hlBtn = document.createElement('button');
        hlBtn.innerHTML = '🖍️';
        hlBtn.title = 'Resaltar';
        hlBtn.onmousedown = (e) => e.preventDefault();
        hlBtn.onclick = () => {
            const picker = document.getElementById('highlight-picker');
            if (picker) picker.style.display = picker.style.display === 'none' ? 'grid' : 'none';
        };
        toolbar.appendChild(hlBtn);

        // Color picker popup
        const colorPicker = document.createElement('div');
        colorPicker.id = 'color-picker';
        colorPicker.className = 'color-picker';
        colorPicker.style.display = 'none';
        TEXT_COLORS.forEach(c => {
            const swatch = document.createElement('button');
            swatch.className = 'color-swatch';
            swatch.style.backgroundColor = c.color;
            swatch.title = c.name;
            swatch.onmousedown = (e) => e.preventDefault();
            swatch.onclick = () => {
                document.execCommand('foreColor', false, c.color);
                colorPicker.style.display = 'none';
                toolbar.style.display = 'none';
            };
            colorPicker.appendChild(swatch);
        });
        toolbar.appendChild(colorPicker);

        // Highlight picker popup
        const hlPicker = document.createElement('div');
        hlPicker.id = 'highlight-picker';
        hlPicker.className = 'color-picker';
        hlPicker.style.display = 'none';
        HIGHLIGHT_COLORS.forEach(c => {
            const swatch = document.createElement('button');
            swatch.className = 'color-swatch';
            swatch.style.backgroundColor = c.color;
            swatch.title = c.name;
            swatch.onmousedown = (e) => e.preventDefault();
            swatch.onclick = () => {
                document.execCommand('hiliteColor', false, c.color);
                hlPicker.style.display = 'none';
                toolbar.style.display = 'none';
            };
            hlPicker.appendChild(swatch);
        });
        toolbar.appendChild(hlPicker);

        document.body.appendChild(toolbar);
        toolbarRef.current = toolbar;

        return () => {
            toolbar.remove();
            toolbarRef.current = null;
        };
    }, []);

    // Handle text selection - show/position toolbar via DOM (no React state!)
    const handleTextSelect = useCallback(() => {
        const selection = window.getSelection();
        const toolbar = toolbarRef.current;
        if (!toolbar) return;

        if (selection && selection.toString().length > 0) {
            const range = selection.getRangeAt(0);
            const rect = range.getBoundingClientRect();
            toolbar.style.display = 'flex';
            toolbar.style.left = `${rect.left + rect.width / 2}px`;
            toolbar.style.top = `${rect.top - 10}px`;
            toolbar.style.transform = 'translate(-50%, -100%)';
        }
    }, []);

    // Hide toolbar when clicking outside
    useEffect(() => {
        const handleMouseDown = (e: MouseEvent) => {
            const toolbar = toolbarRef.current;
            if (toolbar && !toolbar.contains(e.target as Node)) {
                setTimeout(() => {
                    if (!window.getSelection()?.toString()) {
                        toolbar.style.display = 'none';
                        const cp = document.getElementById('color-picker');
                        const hp = document.getElementById('highlight-picker');
                        if (cp) cp.style.display = 'none';
                        if (hp) hp.style.display = 'none';
                    }
                }, 100);
            }
        };
        document.addEventListener('mousedown', handleMouseDown);
        return () => document.removeEventListener('mousedown', handleMouseDown);
    }, []);

    // Debounce ref for auto-save
    const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Auto-save function (debounced)
    const autoSave = useCallback((updatedQuestions: ExamQuestion[]) => {
        // Clear previous timeout
        if (saveTimeoutRef.current) {
            clearTimeout(saveTimeoutRef.current);
        }

        // Debounce: save after 500ms of no changes
        saveTimeoutRef.current = setTimeout(async () => {
            const filename = examKey.replace('file:', '');

            let content: string;
            if (originalData) {
                try {
                    const original = JSON.parse(originalData);
                    if (!Array.isArray(original) && original.preguntas) {
                        content = JSON.stringify({
                            ...original,
                            preguntas: updatedQuestions,
                            total_preguntas: updatedQuestions.length,
                        }, null, 2);
                    } else {
                        content = JSON.stringify(updatedQuestions, null, 2);
                    }
                } catch {
                    content = JSON.stringify(updatedQuestions, null, 2);
                }
            } else {
                content = JSON.stringify(updatedQuestions, null, 2);
            }

            try {
                await writeFile(filename, content);
                console.log('💾 Auto-saved exam');
            } catch (error) {
                console.error('Error auto-saving exam:', error);
            }
        }, 500);
    }, [examKey, originalData]);

    // Cleanup timeout on unmount
    useEffect(() => {
        return () => {
            if (saveTimeoutRef.current) {
                clearTimeout(saveTimeoutRef.current);
            }
        };
    }, []);

    // Mark field as being edited — pauses polling/WS
    const markEditing = useCallback((fieldId: string) => {
        editingFieldRef.current = fieldId;
        setIsExamEditing(true);
        if (editingTimeoutRef.current) clearTimeout(editingTimeoutRef.current);
    }, [setIsExamEditing]);

    // Unmark field as being edited — resumes polling/WS after delay
    const unmarkEditing = useCallback(() => {
        editingFieldRef.current = null;
        // Delay resuming polling so the save has time to propagate
        if (editingTimeoutRef.current) clearTimeout(editingTimeoutRef.current);
        editingTimeoutRef.current = setTimeout(() => {
            setIsExamEditing(false);
        }, 2000);
    }, [setIsExamEditing]);

    // Cleanup editing timeout on unmount
    useEffect(() => {
        return () => {
            if (editingTimeoutRef.current) clearTimeout(editingTimeoutRef.current);
            setIsExamEditing(false);
        };
    }, [setIsExamEditing]);

    // Update question data (with auto-save)
    const handleUpdateQuestion = useCallback((index: number, field: 'pregunta' | 'justificacion', value: string) => {
        const updated = questions.map((q, i) =>
            i === index ? { ...q, [field]: value } : q
        );
        onUpdate(updated);

        // Auto-save changes to file
        autoSave(updated);
    }, [questions, onUpdate, autoSave]);

    // Update option text — deep clone to avoid mutation bugs
    const handleUpdateOption = useCallback((qIndex: number, optIndex: number, value: string) => {
        const updated = questions.map((q, i) => {
            if (i !== qIndex) return q;
            const newOpciones: ExamOption[] = q.opciones.map((opt, j) =>
                j === optIndex ? { ...opt, texto: value } : { ...opt }
            );
            return { ...q, opciones: newOpciones };
        });
        onUpdate(updated);

        // Auto-save option changes too
        autoSave(updated);
    }, [questions, onUpdate, autoSave]);

    // Mark option as correct — deep clone
    const handleMarkCorrect = useCallback((qIndex: number, letra: string) => {
        const updated = questions.map((q, i) =>
            i === qIndex
                ? { ...q, respuesta_correcta: letra.replace(')', '').trim().toUpperCase() }
                : q
        );
        onUpdate(updated);
        autoSave(updated);
    }, [questions, onUpdate, autoSave]);

    // Add option to question — deep clone
    const handleAddOption = useCallback((qIndex: number) => {
        const updated = questions.map((q, i) => {
            if (i !== qIndex) return q;
            const nextLetter = String.fromCharCode(65 + q.opciones.length);
            return {
                ...q,
                opciones: [...q.opciones.map(o => ({ ...o })), { letra: nextLetter, texto: 'Nueva opción' }],
            };
        });
        onUpdate(updated);
        autoSave(updated);
    }, [questions, onUpdate, autoSave]);

    // Remove option from question — deep clone, no splice mutation
    const handleRemoveOption = useCallback((qIndex: number, optIndex: number) => {
        const updated = questions.map((q, i) => {
            if (i !== qIndex) return q;
            const newOpciones = q.opciones
                .filter((_, j) => j !== optIndex)
                .map((opt, j) => ({ ...opt, letra: String.fromCharCode(65 + j) }));
            return { ...q, opciones: newOpciones };
        });
        onUpdate(updated);
        autoSave(updated);
    }, [questions, onUpdate, autoSave]);

    // Delete entire question
    const handleDeleteQuestion = useCallback((index: number) => {
        const confirm = window.confirm(`¿Eliminar pregunta ${index + 1}?`);
        if (!confirm) return;

        const updated = questions.filter((_, i) => i !== index);
        onUpdate(updated);
        autoSave(updated);
    }, [questions, onUpdate, autoSave]);

    // Save exam changes
    const handleSave = useCallback(async () => {
        const filename = examKey.replace('file:', '');

        let content: string;
        if (originalData) {
            try {
                const original = JSON.parse(originalData);
                if (!Array.isArray(original) && original.preguntas) {
                    content = JSON.stringify({
                        ...original,
                        preguntas: questions,
                        total_preguntas: questions.length,
                    }, null, 2);
                } else {
                    content = JSON.stringify(questions, null, 2);
                }
            } catch {
                content = JSON.stringify(questions, null, 2);
            }
        } else {
            content = JSON.stringify(questions, null, 2);
        }

        try {
            await writeFile(filename, content);
            // Show notification (would use a toast system)
            console.log('✅ Exam saved successfully');
        } catch (error) {
            console.error('Error saving exam:', error);
        }
    }, [examKey, questions, originalData]);

    // Handle copy from justification - add reference data to clipboard
    const handleCopyJustification = useCallback((e: React.ClipboardEvent, index: number) => {
        const selection = window.getSelection()?.toString() || '';
        if (!selection) {
            console.log('📋 [Copy] No selection found, skipping');
            return;
        }

        const filename = examKey.replace('file:', '');
        const question = questions[index];

        // Build context with question + actual selected text
        const contextContent = `[Pregunta ${index + 1} de ${filename}]\n${question.pregunta}\n\n[Texto seleccionado de la justificación]:\n${selection}`;

        console.log('📋 [Copy] Selection:', selection.substring(0, 80) + '...');
        console.log('📋 [Copy] Context length:', contextContent.length);
        console.log('📋 [Copy] Question:', question.pregunta.substring(0, 50));

        // Add custom data for paste handler in ChatPanel
        const refData = {
            examKey,
            filename,
            questionIndex: index,
            preview: selection.substring(0, 100) + (selection.length > 100 ? '...' : ''),
            content: contextContent,
        };

        e.clipboardData.setData('application/x-exam-justification', JSON.stringify(refData));
        e.clipboardData.setData('text/plain', selection);  // Keep normal text as fallback
        e.preventDefault();
        console.log('📋 [Copy] Clipboard data set successfully');
    }, [examKey, questions]);
    // Attach question to agent chat
    const handleAttachQuestion = useCallback((index: number) => {
        const question = questions[index];
        const filename = examKey.replace('file:', '');

        const optionsText = question.opciones.map(o => `${o.letra}) ${o.texto}`).join('\n');
        const fullContent = `PREGUNTA ${index + 1} (de ${filename}):
${question.pregunta}

OPCIONES:
${optionsText}

RESPUESTA CORRECTA: ${question.respuesta_correcta}`;

        addSnippetRef({
            source: examKey,
            type: 'exam_question',
            name: filename,
            startLine: index + 1,
            endLine: index + 1,
            preview: `Pregunta ${index + 1}: ${question.pregunta.substring(0, 50)}...`,
            content: fullContent,
            isExamQuestion: true,
            questionIndex: index,
        });

        // Set up pending analysis - will be activated when user sends message
        // DON'T set currentAnalyzingIndex here - wait until user sends
        setAnalysisMode('append');
        setPendingExamAnalysis({
            examKey,
            examData: JSON.stringify(questions),
            originalExamData: originalData,
            questionIndex: index,
        });

        // Let user type their own question
        setChatInputValue('');

        // Focus the chat input so user can type
        setTimeout(() => {
            const chatInput = document.querySelector('.chat-input textarea') as HTMLTextAreaElement;
            if (chatInput) {
                chatInput.focus();
                chatInput.placeholder = 'Escribe tu pregunta sobre esta pregunta de examen...';
            }
        }, 100);
    }, [questions, examKey, originalData, addSnippetRef, setAnalysisMode, setPendingExamAnalysis, setChatInputValue]);

    // Analyze with agent
    const handleAnalyzeQuestion = useCallback((index: number) => {
        const question = questions[index];
        const optionsText = question.opciones.map(o => `${o.letra}) ${o.texto}`).join('\n');

        const prompt = `INSTRUCCIONES IMPORTANTES:
- Eres un experto médico analizando una pregunta de examen.
- Proporciona ÚNICAMENTE la explicación médica/científica.
- NO incluyas código, instrucciones de herramientas, ni comentarios sobre cómo guardar la respuesta.

PREGUNTA ${index + 1}:
${question.pregunta}

OPCIONES:
${optionsText}

TAREA:
1. Indica cuál es la respuesta correcta.
2. Explica brevemente POR QUÉ es correcta.
3. Menciona por qué las otras opciones son incorrectas (opcional).

Tu respuesta (solo contenido médico):`;

        // Set up for analysis - React will show existing, DOM direct handles streaming
        setCurrentAnalyzingIndex(index);
        setAnalysisMode('append');
        setPendingExamAnalysis({
            examKey,
            examData: JSON.stringify(questions),
            originalExamData: originalData,
            questionIndex: index,
        });

        // Set message and trigger send via store
        setChatInputValue(prompt);
        // Small delay to let state propagate then trigger send
        setTimeout(() => triggerSend(), 50);
    }, [questions, examKey, originalData, setCurrentAnalyzingIndex, setAnalysisMode, setPendingExamAnalysis, setChatInputValue, triggerSend]);

    // Is option correct?
    const isCorrect = (question: ExamQuestion, letra: string) => {
        const rc = question.respuesta_correcta;
        const normLetra = letra.replace(')', '').trim().toUpperCase();

        if (Array.isArray(rc)) {
            return rc.map(r => r.toUpperCase()).includes(normLetra);
        }
        return String(rc).toUpperCase() === normLetra;
    };

    return (
        <div className="exam-container">
            {/* Format toolbar is created via DOM in useEffect - no JSX needed */}

            {questions.map((question, idx) => (
                <div key={idx} className="exam-card" id={`card-${idx}`}>
                    {/* Header */}
                    <div className="card-header">
                        <span className="question-number">Pregunta {idx + 1}</span>
                        <button
                            className="btn-delete-question"
                            onClick={() => handleDeleteQuestion(idx)}
                            title="Eliminar pregunta completa"
                        >
                            <Trash2 size={14} /> Eliminar
                        </button>
                    </div>

                    {/* Question Text — uncontrolled contentEditable */}
                    <EditableText
                        className="exam-question"
                        value={question.pregunta || ''}
                        placeholder="Sin pregunta"
                        onFocus={() => markEditing(`q-${idx}`)}
                        onCommit={(text) => {
                            handleUpdateQuestion(idx, 'pregunta', text);
                            unmarkEditing();
                        }}
                    />

                    {/* Options */}
                    <div className="exam-options">
                        {(question.opciones || []).map((opt, optIdx) => {
                            const correct = isCorrect(question, opt.letra);
                            const fieldId = `opt-${idx}-${optIdx}`;
                            return (
                                <div
                                    key={`${idx}-${optIdx}`}
                                    className={`exam-option ${correct ? 'correct-answer' : ''}`}
                                    id={`option-${idx}-${optIdx}`}
                                >
                                    <div className="option-letter">{opt.letra}</div>
                                    <EditableText
                                        className="option-text"
                                        value={opt.texto || ''}
                                        richText
                                        onFocus={() => markEditing(fieldId)}
                                        onMouseUp={handleTextSelect}
                                        onCommit={(text) => {
                                            handleUpdateOption(idx, optIdx, text);
                                            unmarkEditing();
                                        }}
                                    />
                                    <button
                                        className="btn-mark-correct"
                                        onClick={() => handleMarkCorrect(idx, opt.letra)}
                                        title={correct ? 'Respuesta correcta' : 'Marcar como correcta'}
                                    >
                                        {correct ? '✓ Correcta' : '○'}
                                    </button>
                                    <button
                                        className="btn-delete-option"
                                        onClick={() => handleRemoveOption(idx, optIdx)}
                                        title="Eliminar opción"
                                    >
                                        <Trash2 size={12} />
                                    </button>
                                </div>
                            );
                        })}

                        <button
                            className="btn-add-option"
                            onClick={() => handleAddOption(idx)}
                            title="Añadir nueva opción"
                        >
                            <Plus size={14} /> Añadir opción
                        </button>
                    </div>

                    {/* Justification Section - show streaming or saved */}
                    {(() => {
                        const isAnalyzingThis = currentAnalyzingIndex === idx;
                        const wasJustAnalyzed = lastAnalyzedIndex === idx;
                        const existingJustification = question.justificacion || '';

                        // When analyzing: React renders ONLY existing (DOM direct handles streaming)
                        // When just analyzed: show streamingJustification (which has combined content)
                        // Otherwise: show saved justificacion (from file/polling)
                        let displayContent = '';
                        if (isAnalyzingThis) {
                            // React only shows existing - DOM direct will add streaming below
                            displayContent = existingJustification;
                        } else if (wasJustAnalyzed && streamingJustification) {
                            // Just finished analyzing - streamingJustification has combined content
                            displayContent = filterToolCode(streamingJustification);
                        } else {
                            displayContent = existingJustification;
                        }

                        // Show container if: analyzing, has content, or just analyzed with streaming
                        const shouldShow = isAnalyzingThis || displayContent || (wasJustAnalyzed && streamingJustification);
                        if (!shouldShow) return null;

                        return (
                            <div className="analysis-section" id={`analysis-section-${idx}`}>
                                <div className="analysis-label">
                                    JUSTIFICACIÓN (LLM):
                                    {isAnalyzingThis && (
                                        <span className="streaming-indicator"> ⚡ Analizando...</span>
                                    )}
                                </div>
                                {/* Existing content - uncontrolled contentEditable, never re-mounted on edit */}
                                {displayContent && (() => {
                                    const justFieldId = `just-${idx}`;
                                    return (
                                        <EditableText
                                            className="justification-text existing-content"
                                            value={displayContent}
                                            disabled={isAnalyzingThis}
                                            preserveLineBreaks
                                            richText
                                            onFocus={() => markEditing(justFieldId)}
                                            onCommit={(newText) => {
                                                const normalizeText = (t: string) => t.replace(/\r\n/g, '\n').replace(/\s+$/gm, '').trim();
                                                const oldNorm = normalizeText(existingJustification);
                                                const newNorm = normalizeText(newText);
                                                if (oldNorm !== newNorm) {
                                                    handleUpdateQuestion(idx, 'justificacion', newText);
                                                }
                                                unmarkEditing();
                                            }}
                                            onCopy={(e) => handleCopyJustification(e, idx)}
                                            onMouseUp={handleTextSelect}
                                        />
                                    );
                                })()}
                                {/* Streaming content - DOM controlled, separate from existing */}
                                {isAnalyzingThis && (
                                    <>
                                        {displayContent && <hr style={{ borderColor: 'var(--border-highlight)', opacity: 0.5, margin: '12px 0' }} />}
                                        <div className="streaming-label" style={{ fontSize: '11px', color: 'var(--accent-blue)', marginBottom: '4px' }}>
                                            📝 Nueva respuesta:
                                        </div>
                                        <div
                                            className="justification-text streaming"
                                            id={`justification-${idx}`}
                                        >
                                            <span className="streaming-placeholder">Esperando respuesta...</span>
                                        </div>
                                    </>
                                )}
                                {/* When not analyzing and no content, show nothing */}
                                {!isAnalyzingThis && !displayContent && (
                                    <div className="justification-text" id={`justification-${idx}`} />
                                )}

                                {/* A2UI Image Gallery - shows relevant medical images */}
                                {a2uiImagesEnabled && displayContent && !isAnalyzingThis && (
                                    <A2UIImageGallery
                                        questionText={question.pregunta}
                                        justificationText={displayContent}
                                        questionIndex={idx}
                                    />
                                )}
                            </div>
                        );
                    })()}

                    {/* Action Buttons */}
                    <div className="card-actions">
                        <button
                            className="btn-analyze"
                            style={{ background: 'var(--accent-teal)' }}
                            onClick={() => handleAttachQuestion(idx)}
                        >
                            <FileText size={14} /> Adjuntar
                        </button>
                        <button
                            className="btn-analyze"
                            onClick={() => handleAnalyzeQuestion(idx)}
                        >
                            <Brain size={14} /> Analizar con Agente
                        </button>
                        <button
                            className="btn-analyze"
                            style={{ background: 'var(--accent-blue)' }}
                            onClick={handleSave}
                        >
                            <Save size={14} /> Guardar
                        </button>
                    </div>
                </div>
            ))}
        </div>
    );
});

// Uncontrolled contentEditable: sets value once on mount and only re-syncs from
// the outside when the user is NOT focused. This stops auto-save / parent
// re-renders from wiping in-progress typing.
//
// `richText` mode persists inline HTML formatting (bold, italic, color, highlight).
// Storage is sanitized with DOMPurify on every commit and on every external render,
// so even if a malicious value reaches the field it is stripped before display.
interface EditableTextProps {
    className?: string;
    value: string;
    placeholder?: string;
    disabled?: boolean;
    preserveLineBreaks?: boolean;
    richText?: boolean;
    onFocus?: () => void;
    onCommit: (text: string) => void;
    onCopy?: (e: React.ClipboardEvent<HTMLDivElement>) => void;
    onMouseUp?: () => void;
}

// Detect whether a stored value already contains rich-text markup. Older exams
// were saved as plain strings, so we render those as text to avoid surprises.
const looksLikeHtml = (s: string): boolean => /<\/?(b|strong|i|em|u|mark|span|br|div|font)\b/i.test(s);

const EditableText = memo(function EditableText({
    className,
    value,
    placeholder,
    disabled,
    preserveLineBreaks,
    richText,
    onFocus,
    onCommit,
    onCopy,
    onMouseUp,
}: EditableTextProps) {
    const ref = useRef<HTMLDivElement>(null);
    const isFocusedRef = useRef(false);
    const lastCommittedRef = useRef<string>(value);

    const renderInto = useCallback((el: HTMLDivElement, text: string) => {
        if (richText && looksLikeHtml(text)) {
            el.innerHTML = sanitizeRich(text);
            return;
        }
        if (preserveLineBreaks || richText) {
            el.textContent = '';
            const parts = (text || '').split('\n');
            parts.forEach((part, i) => {
                if (i > 0) el.appendChild(document.createElement('br'));
                if (part) el.appendChild(document.createTextNode(part));
            });
        } else {
            el.textContent = text;
        }
    }, [preserveLineBreaks, richText]);

    // Mount: paint initial value once.
    useLayoutEffect(() => {
        if (!ref.current) return;
        renderInto(ref.current, value || '');
        lastCommittedRef.current = value || '';
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // External updates: only re-sync DOM when the user is NOT editing this field
    // and the incoming value actually differs from what we last committed.
    useLayoutEffect(() => {
        if (!ref.current) return;
        if (isFocusedRef.current) return;
        if (value === lastCommittedRef.current) return;
        renderInto(ref.current, value || '');
        lastCommittedRef.current = value || '';
    }, [value, renderInto]);

    return (
        <div
            ref={ref}
            className={className}
            contentEditable={!disabled}
            suppressContentEditableWarning
            data-placeholder={placeholder}
            onFocus={() => {
                isFocusedRef.current = true;
                onFocus?.();
            }}
            onBlur={(e) => {
                isFocusedRef.current = false;
                let next: string;
                if (richText) {
                    const raw = e.currentTarget.innerHTML;
                    next = sanitizeRich(raw);
                    // Collapse to plain text if no actual formatting survived sanitization.
                    if (!looksLikeHtml(next)) {
                        next = e.currentTarget.innerText;
                    }
                } else {
                    next = e.currentTarget.innerText;
                }
                lastCommittedRef.current = next;
                onCommit(next);
            }}
            onCopy={onCopy}
            onMouseUp={onMouseUp}
        />
    );
});
