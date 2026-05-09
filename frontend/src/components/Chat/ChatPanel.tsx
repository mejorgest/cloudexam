import { useState, useRef, useCallback, useEffect, useMemo, memo } from 'react';
import { useAppStore } from '../../store/appStore';
import { askAgentStream, readFile, writeFile } from '../../services/api';
import type { ExamQuestion } from '../../types';
import { marked } from 'marked';
import { Send, Plus, X, Paperclip } from 'lucide-react';
import { ContextIndicator } from './ContextIndicator';

export function ChatPanel() {
    const {
        messages,
        addMessage,
        updateMessage,
        attachedFiles,
        snippetRefs,
        addAttachedFile,
        removeAttachedFile,
        removeSnippetRef,
        clearSnippetRefs,
        addSnippetRef,
        isLoading,
        setIsLoading,
        selectedKey,
        currentAnalyzingIndex,
        setCurrentAnalyzingIndex,
        analysisMode,
        setAnalysisMode,
        pendingExamAnalysis,
        chatInputValue,
        shouldTriggerSend,
        setChatInputValue,
        resetTriggerSend,
        setStreamingJustification,
        setLastAnalyzedIndex,
    } = useAppStore();

    const [inputValue, setInputValue] = useState('');
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const attachInputRef = useRef<HTMLInputElement>(null);

    // Upload a JSON file: save to workspace + attach to chat context.
    const handleAttachFile = useCallback(async (file: File) => {
        if (!file.name.toLowerCase().endsWith('.json')) {
            alert('Solo se aceptan archivos .json de exámenes.');
            return;
        }
        try {
            const content = await file.text();
            // Validate it parses
            try {
                JSON.parse(content);
            } catch {
                alert('El archivo no es JSON válido.');
                return;
            }
            await writeFile(file.name, content);
            addAttachedFile({ type: 'file', name: file.name, content });
        } catch (e) {
            console.error('Error subiendo archivo:', e);
            alert(`Error subiendo el archivo: ${e instanceof Error ? e.message : e}`);
        } finally {
            if (attachInputRef.current) attachInputRef.current.value = '';
        }
    }, [addAttachedFile]);

    // Sync input value from store (for external triggers like ExamViewer)
    useEffect(() => {
        if (chatInputValue && chatInputValue !== inputValue) {
            setInputValue(chatInputValue);
            setChatInputValue(''); // Clear store value after syncing
        }
    }, [chatInputValue, inputValue, setChatInputValue]);

    // Scroll to bottom when messages change - but NOT during exam analysis
    const isAnyStreaming = messages.some(m => m.isStreaming);
    useEffect(() => {
        // Don't auto-scroll during exam question analysis - keep user focused on exam card
        if (currentAnalyzingIndex !== null) {
            return;
        }

        if (isAnyStreaming) {
            // During streaming, scroll instantly without animation
            messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
        } else {
            // After streaming, use smooth scroll
            messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
        }
    }, [messages, isAnyStreaming, currentAnalyzingIndex]);

    // Handle external trigger to send (from ExamViewer)
    const handleSendRef = useRef<(() => void) | undefined>(undefined);

    // Send message
    const handleSend = useCallback(async () => {
        const message = inputValue.trim();
        if (!message || isLoading) return;

        // If there's a pending exam analysis (from Attach button), activate the analyzing index now
        if (pendingExamAnalysis && pendingExamAnalysis.questionIndex !== undefined) {
            setCurrentAnalyzingIndex(pendingExamAnalysis.questionIndex);
            setStreamingJustification('');  // Clear any previous
        }

        setIsLoading(true);
        setInputValue('');

        // Build display message with attachments
        let displayMsg = message;
        if (attachedFiles.length > 0) {
            const tags = attachedFiles.map(f => `@${f.type}:${f.name}`).join(' ');
            displayMsg = `${tags}\n${message}`;
        }
        if (snippetRefs.length > 0) {
            const snippetTags = snippetRefs.map(s => `📎${s.name} [${s.startLine}-${s.endLine}]`).join(' ');
            displayMsg = `${snippetTags}\n${displayMsg}`;
        }

        // Add user message
        addMessage({
            id: `user-${Date.now()}`,
            type: 'user',
            content: displayMsg,
            timestamp: new Date(),
        });

        // Create streaming message placeholder
        const streamMsgId = `stream-${Date.now()}`;
        addMessage({
            id: streamMsgId,
            type: 'assistant',
            content: '',
            timestamp: new Date(),
            isStreaming: true,
        });

        try {
            // Build context
            const contextItems: { name: string; content: string }[] = [];

            // Add attached files
            for (const f of attachedFiles) {
                contextItems.push({
                    name: `${f.type}:${f.name}`,
                    content: f.content,
                });
            }

            // Add snippets
            for (const s of snippetRefs) {
                contextItems.push({
                    name: `snippet:${s.name} [${s.startLine}-${s.endLine}]`,
                    content: `[FRAGMENTO SELECCIONADO de ${s.name}, líneas ${s.startLine}-${s.endLine}]:\n${s.content}`,
                });
            }

            // Auto-attach current tab (file) if no context and not analyzing exam
            if (contextItems.length === 0 && selectedKey && currentAnalyzingIndex === null) {
                const isExamFile = selectedKey.toLowerCase().includes('examen') ||
                    selectedKey.toLowerCase().includes('pregunta') ||
                    selectedKey.endsWith('.json');

                if (!isExamFile && selectedKey.startsWith('file:')) {
                    const name = selectedKey.replace('file:', '');
                    try {
                        const data = await readFile(name);
                        const content = data.content || '';
                        if (content) {
                            contextItems.push({ name, content });
                        }
                    } catch { /* ignore */ }
                }
            }

            // Stream response - use DIRECT DOM manipulation to bypass React overhead
            let fullResponse = '';
            let lastExamUpdateTime = 0;
            // Use pendingExamAnalysis directly since currentAnalyzingIndex may not be updated yet in React state
            const activeExamIndex = pendingExamAnalysis?.questionIndex ?? currentAnalyzingIndex;
            const isAnalyzingExam = activeExamIndex !== null && activeExamIndex !== undefined;
            const EXAM_UPDATE_INTERVAL = 50;  // Update exam every 50ms with DOM direct

            // Get DOM element for direct updates (bypasses React)
            const getStreamingElement = () => {
                return document.querySelector(`[data-msg-id="${streamMsgId}"] .streaming-content`);
            };

            // Get exam justification element for direct updates
            const getJustificationElement = () => {
                if (activeExamIndex === null || activeExamIndex === undefined) return null;
                return document.getElementById(`justification-${activeExamIndex}`);
            };

            // Filter tool code from content for display
            const filterForDisplay = (content: string): string => {
                let result = content;
                result = result.replace(/```python[\s\S]*?```\n*/g, '');
                result = result.replace(/```python[\s\S]*$/g, '');
                result = result.replace(/<div class="tool-status[^"]*">[^<]*<\/div>\n*/g, '');
                result = result.replace(/<div class="tool-indicator">[^<]*<\/div>\n*/g, '');
                result = result.replace(/\n{3,}/g, '\n\n');
                return result.trim();
            };

            // Direct DOM update for chat - no React involved
            const updateDOM = (content: string) => {
                const el = getStreamingElement();
                if (el) {
                    el.innerHTML = transformToolBlocks(content);
                }
            };

            // Direct DOM update for exam justification - writes ONLY new streaming content
            // Existing content is now handled by React in a separate div
            const updateExamDOM = (content: string) => {
                const el = getJustificationElement();
                if (el) {
                    const filtered = filterForDisplay(content);
                    // Escape HTML for safe display
                    const escaped = filtered.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    el.innerHTML = escaped.replace(/\n/g, '<br>');
                }
                lastExamUpdateTime = Date.now();
            };

            for await (const chunk of askAgentStream({
                question: message,
                context_files: contextItems.length > 0 ? contextItems : undefined,
            })) {
                switch (chunk.type) {
                    case 'chunk':
                        fullResponse += chunk.content || '';
                        // Update DOM directly (no React overhead)
                        updateDOM(fullResponse);
                        // Throttle exam DOM updates
                        const now = Date.now();
                        if (isAnalyzingExam && now - lastExamUpdateTime >= EXAM_UPDATE_INTERVAL) {
                            updateExamDOM(fullResponse);
                        }
                        break;

                    case 'tool_start':
                        fullResponse += `\n<div class="tool-status executing">⚙️ ${chunk.content}...</div>\n`;
                        updateDOM(fullResponse);
                        // Also update exam on tool events
                        if (isAnalyzingExam) updateExamDOM(fullResponse);
                        break;

                    case 'tool_result':
                        const resultIcon = chunk.success ? '✅' : '❌';
                        const resultClass = chunk.success ? 'success' : 'error';
                        fullResponse = fullResponse.replace(
                            /<div class="tool-status executing">.*?<\/div>\n?/gs,
                            `<div class="tool-status ${resultClass}">${resultIcon} ${(chunk.content || '').split('\n')[0]}</div>\n\n`
                        );
                        updateDOM(fullResponse);
                        // Also update exam on tool events
                        if (isAnalyzingExam) updateExamDOM(fullResponse);
                        break;

                    case 'checkpoint':
                        updateMessage(streamMsgId, { checkpoint: chunk.content });
                        break;

                    case 'done':
                        // Final update through React to sync state
                        updateMessage(streamMsgId, { content: fullResponse, isStreaming: false });
                        // Final update to React state for persistence
                        if (isAnalyzingExam) {
                            setStreamingJustification(fullResponse);
                        }
                        break;

                    case 'error':
                        fullResponse += `\n\n❌ Error: ${chunk.content}`;
                        updateMessage(streamMsgId, { content: fullResponse, isStreaming: false });
                        break;
                }
            }

            // Handle exam question analysis save
            if (isAnalyzingExam && pendingExamAnalysis?.examKey) {
                await saveAnalysisToQuestion(fullResponse);
                // Note: Don't clear streamingJustification here - let it persist
                // The ExamViewer will show saved justificacion once currentAnalyzingIndex is cleared
            }

            // Clear snippets after each message
            clearSnippetRefs();

        } catch (error) {
            updateMessage(streamMsgId, {
                content: `❌ Error: ${error instanceof Error ? error.message : 'Unknown error'}`,
                isStreaming: false,
            });
        } finally {
            setIsLoading(false);
            // Save which question was analyzed before clearing
            const analyzedIndex = pendingExamAnalysis?.questionIndex;
            if (analyzedIndex !== null && analyzedIndex !== undefined) {
                setLastAnalyzedIndex(analyzedIndex);
            }
            // Clear analyzing index - ExamViewer will use lastAnalyzedIndex + streamingJustification
            setCurrentAnalyzingIndex(null);
            setAnalysisMode(null);
        }
    }, [
        inputValue, isLoading, attachedFiles, snippetRefs, selectedKey,
        currentAnalyzingIndex, analysisMode, pendingExamAnalysis,
        addMessage, updateMessage, setIsLoading, clearSnippetRefs,
        setCurrentAnalyzingIndex, setAnalysisMode,
    ]);

    // Keep ref updated with latest handleSend
    handleSendRef.current = handleSend;

    // Listen for external trigger to send
    useEffect(() => {
        if (shouldTriggerSend && inputValue.trim()) {
            resetTriggerSend();
            handleSendRef.current?.();
        }
    }, [shouldTriggerSend, inputValue, resetTriggerSend]);

    // Save analysis to exam question
    // CRITICAL: Read CURRENT file from disk, not the stale snapshot from pendingExamAnalysis.
    // This ensures existing justification content (including A2UI edits) is never lost.
    const saveAnalysisToQuestion = async (response: string) => {
        if (!pendingExamAnalysis.examKey || pendingExamAnalysis.questionIndex === null) return;

        // Filter out tool code blocks from the response
        const filterToolCode = (content: string): string => {
            let result = content;
            result = result.replace(/```python[\s\S]*?```\n*/g, '');
            result = result.replace(/```python[\s\S]*$/g, '');
            result = result.replace(/<div class="tool-status[^"]*">[^<]*<\/div>\n*/g, '');
            result = result.replace(/<div class="tool-indicator">[^<]*<\/div>\n*/g, '');
            result = result.replace(/\n{3,}/g, '\n\n');
            return result.trim();
        };

        const cleanedResponse = filterToolCode(response);
        if (!cleanedResponse) return;  // Don't save empty responses

        const filename = pendingExamAnalysis.examKey.replace('file:', '');
        const qIndex = pendingExamAnalysis.questionIndex;

        try {
            // Read the CURRENT file from disk to get the latest state
            const { writeFile, readFile } = await import('../../services/api');
            let questions: ExamQuestion[];
            let originalWrapper: Record<string, unknown> | null = null;

            try {
                const fileData = await readFile(filename);
                const parsed = JSON.parse(fileData.content || '[]');
                if (Array.isArray(parsed)) {
                    questions = parsed;
                } else if (parsed.preguntas && Array.isArray(parsed.preguntas)) {
                    questions = parsed.preguntas;
                    originalWrapper = parsed;
                } else {
                    questions = JSON.parse(pendingExamAnalysis.examData || '[]');
                }
            } catch {
                // Fallback to snapshot if file read fails
                console.warn('⚠️ Could not read current file, using snapshot');
                questions = JSON.parse(pendingExamAnalysis.examData || '[]');
            }

            if (questions[qIndex]) {
                // Get the CURRENT justification from disk (not the stale snapshot)
                const existing = questions[qIndex].justificacion || '';
                const newJustification = analysisMode === 'append' && existing
                    ? `${existing}\n\n---\n\n${cleanedResponse}`
                    : cleanedResponse;

                questions[qIndex].justificacion = newJustification;

                // Build save content
                let content: string;
                if (originalWrapper) {
                    content = JSON.stringify({ ...originalWrapper, preguntas: questions, total_preguntas: questions.length }, null, 2);
                } else if (pendingExamAnalysis.originalExamData) {
                    try {
                        const original = JSON.parse(pendingExamAnalysis.originalExamData);
                        if (!Array.isArray(original) && original.preguntas) {
                            content = JSON.stringify({ ...original, preguntas: questions }, null, 2);
                        } else {
                            content = JSON.stringify(questions, null, 2);
                        }
                    } catch {
                        content = JSON.stringify(questions, null, 2);
                    }
                } else {
                    content = JSON.stringify(questions, null, 2);
                }

                await writeFile(filename, content);

                // Update streaming justification with the combined content to persist display
                setStreamingJustification(newJustification);

                console.log('✅ Analysis saved to question', qIndex + 1);
            }
        } catch (e) {
            console.error('Error saving analysis to question:', e);
        }
    };

    // Handle keyboard shortcut
    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    // Handle paste - detect if it's from a justification and convert to reference
    const handlePaste = (e: React.ClipboardEvent) => {
        const clipboardData = e.clipboardData;

        // Check if there's custom exam justification data
        const examRef = clipboardData.getData('application/x-exam-justification');
        const plainText = clipboardData.getData('text/plain');
        console.log('📎 [Paste] examRef present:', !!examRef, '| plainText length:', plainText?.length || 0);

        if (examRef) {
            e.preventDefault();
            try {
                const refData = JSON.parse(examRef);
                console.log('📎 [Paste] Parsed ref data - preview:', refData.preview);
                console.log('📎 [Paste] Content length:', refData.content?.length || 0);
                console.log('📎 [Paste] Content preview:', refData.content?.substring(0, 120));

                // Add as snippet reference instead of pasting text
                addSnippetRef({
                    source: refData.examKey,
                    type: 'exam_question',
                    name: refData.filename,
                    startLine: refData.questionIndex + 1,
                    endLine: refData.questionIndex + 1,
                    preview: `Justificación P${refData.questionIndex + 1}: ${refData.preview}`,
                    content: refData.content,
                    isExamQuestion: true,
                    questionIndex: refData.questionIndex,
                });
                console.log('📎 [Paste] Snippet ref added successfully');
                return;
            } catch (err) {
                console.warn('📎 [Paste] Failed to parse exam ref:', err);
                // If parsing fails, fall through to normal paste
            }
        }

        // Normal paste behavior
    };



    // Quick actions
    const quickActions = [
        { label: '📋 Estado', action: () => setInputValue('¿Qué hay en el estado actual?') },
        { label: '🔍 Buscar', action: () => setInputValue('Busca información sobre ') },
        { label: '📝 Resumir', action: () => setInputValue('Resume el contenido seleccionado') },
    ];

    return (
        <div className="chat-panel">
            {/* Header */}
            <div className="chat-header">
                <div className="chat-title">
                    <span>💬</span> Agent Chat
                </div>
                <ContextIndicator />
            </div>

            {/* Messages */}
            <div className="chat-messages" id="chatMessages">
                {messages.map(msg => (
                    <MessageItem key={msg.id} message={msg} />
                ))}
                <div ref={messagesEndRef} />
            </div>

            {/* Quick Actions */}
            <div className="quick-actions">
                {quickActions.map((action, i) => (
                    <button key={i} className="quick-action" onClick={action.action}>
                        {action.label}
                    </button>
                ))}
            </div>

            {/* Attached Files */}
            {attachedFiles.length > 0 && (
                <div className="attached-files">
                    {attachedFiles.map((file, i) => (
                        <div key={i} className="attached-file">
                            <Paperclip size={12} />
                            <span>{file.type}:{file.name}</span>
                            <button className="remove-btn" onClick={() => removeAttachedFile(i)}>
                                <X size={14} />
                            </button>
                        </div>
                    ))}
                </div>
            )}

            {/* Snippet References */}
            {snippetRefs.length > 0 && (
                <div className="snippet-refs">
                    {snippetRefs.map((ref, i) => (
                        <div key={i} className="snippet-ref">
                            📎 {ref.name} [{ref.startLine}-{ref.endLine}]
                            <button className="remove-btn" onClick={() => removeSnippetRef(i)}>
                                <X size={14} />
                            </button>
                        </div>
                    ))}
                </div>
            )}

            {/* Input */}
            <div className="chat-input-container">
                <div className="chat-input-row">
                    <input
                        ref={attachInputRef}
                        type="file"
                        accept="application/json,.json"
                        style={{ display: 'none' }}
                        onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (f) handleAttachFile(f);
                        }}
                    />
                    <button
                        className="status-btn"
                        onClick={() => attachInputRef.current?.click()}
                        title="Subir un JSON de examen y adjuntarlo al chat"
                        style={{ padding: '10px' }}
                    >
                        <Plus size={18} />
                    </button>
                    <textarea
                        ref={inputRef}
                        id="chatInput"
                        className="chat-input"
                        placeholder="Escribe tu mensaje..."
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        onKeyDown={handleKeyDown}
                        onPaste={handlePaste}
                        rows={1}
                    />
                    <button
                        id="sendBtn"
                        className="chat-send-btn"
                        onClick={handleSend}
                        disabled={isLoading || !inputValue.trim()}
                    >
                        {isLoading ? (
                            <div className="loading-dots">
                                <span></span>
                                <span></span>
                                <span></span>
                            </div>
                        ) : (
                            <>
                                <Send size={16} />
                                Enviar
                            </>
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ============== Memoized Message Component ==============
interface MessageItemProps {
    message: {
        id: string;
        type: 'user' | 'assistant' | 'system';
        content: string;
        isStreaming?: boolean;
        checkpoint?: string;
    };
}

// Tool patterns to detect and replace with indicators
const TOOL_PATTERNS: { pattern: RegExp; icon: string; label: string }[] = [
    { pattern: /rag_search\s*\(/i, icon: '📚', label: 'Buscando en documentos PDF...' },
    { pattern: /google_search\s*\(/i, icon: '🌐', label: 'Buscando en la web...' },
    { pattern: /read_file\s*\(/i, icon: '📄', label: 'Leyendo archivo...' },
    { pattern: /write_file\s*\(/i, icon: '💾', label: 'Escribiendo archivo...' },
    { pattern: /execute_python\s*\(/i, icon: '🐍', label: 'Ejecutando código...' },
    { pattern: /from\s+servers\.\w+\s+import/i, icon: '⚙️', label: 'Preparando herramienta...' },
];

// Transform content to replace tool code blocks with indicators
function transformToolBlocks(content: string): string {
    let result = content;

    // First, handle COMPLETE code blocks (```python ... ```)
    result = result.replace(
        /```python\s*([\s\S]*?)```/g,
        (match, codeContent) => {
            for (const { pattern, icon, label } of TOOL_PATTERNS) {
                if (pattern.test(codeContent)) {
                    return `<div class="tool-indicator">${icon} ${label}</div>`;
                }
            }
            return match;
        }
    );

    // Then, handle INCOMPLETE code blocks during streaming (```python ... without closing ```)
    // This detects when streaming is showing code that hasn't closed yet
    const incompleteMatch = result.match(/```python\s*([\s\S]*)$/);
    if (incompleteMatch) {
        const codeContent = incompleteMatch[1];
        for (const { pattern, icon, label } of TOOL_PATTERNS) {
            if (pattern.test(codeContent)) {
                // Replace the incomplete block with an indicator
                result = result.replace(/```python\s*[\s\S]*$/, `<div class="tool-indicator">${icon} ${label}</div>`);
                break;
            }
        }
    }

    return result;
}

const MessageItem = memo(function MessageItem({ message }: MessageItemProps) {
    // Only parse markdown when NOT streaming - major performance optimization
    const htmlContent = useMemo(() => {
        if (message.type === 'user') return null;
        // Skip heavy markdown parsing during streaming
        if (message.isStreaming) return null;
        try {
            const transformedContent = transformToolBlocks(message.content || '');
            return marked.parse(transformedContent) as string;
        } catch {
            return null;
        }
    }, [message.content, message.type, message.isStreaming]);

    // For streaming, just transform tool blocks but don't parse markdown
    const streamingContent = useMemo(() => {
        if (!message.isStreaming || message.type === 'user') return null;
        return transformToolBlocks(message.content || '');
    }, [message.content, message.isStreaming, message.type]);

    return (
        <div className={`message-wrapper ${message.type}`} data-msg-id={message.id}>
            <div className={`message ${message.type} ${message.isStreaming ? 'streaming' : ''}`}>
                {message.type === 'user' ? (
                    <div>{message.content}</div>
                ) : message.isStreaming ? (
                    // During streaming: show raw text with tool indicators (no markdown parsing)
                    <>
                        <div
                            className="streaming-content"
                            dangerouslySetInnerHTML={{ __html: streamingContent || message.content }}
                        />
                        <span className="streaming-cursor">▌</span>
                    </>
                ) : htmlContent ? (
                    // After streaming complete: show parsed markdown
                    <>
                        <div dangerouslySetInnerHTML={{ __html: htmlContent }} />
                        {message.checkpoint && (
                            <div className="message-checkpoint" title={`Checkpoint: ${message.checkpoint}`}>
                                {message.checkpoint.substring(0, 8)}
                            </div>
                        )}
                    </>
                ) : (
                    <div>{message.content}</div>
                )}
            </div>
        </div>
    );
});
