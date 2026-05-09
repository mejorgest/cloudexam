// ============== API Types ==============

export interface WorkspaceFile {
    name: string;
    size?: number;
}

export interface ContextInfo {
    token_count: number;
    max_tokens: number;
    non_system_messages: number;
    max_messages: number;
    needs_compaction: boolean;
    compaction_count?: number;
}

export interface ChangelogEntry {
    timestamp: string;
    operation: string;
    target: string;
    details?: string;
}

// ============== Exam Types ==============

export interface ExamOption {
    letra: string;
    texto: string;
}

export interface ExamQuestion {
    pregunta: string;
    opciones: ExamOption[];
    respuesta_correcta: string | string[];
    justificacion?: string;
}

export type ExamData = ExamQuestion[] | {
    preguntas: ExamQuestion[];
    total_preguntas?: number;
    [key: string]: unknown;
};

// ============== Chat Types ==============

export interface ChatMessage {
    id: string;
    type: 'user' | 'assistant' | 'system';
    content: string;
    timestamp: Date;
    toolsUsed?: string[];
    checkpoint?: string;
    isStreaming?: boolean;
}

export interface AttachedFile {
    type: 'file';
    name: string;
    content: string;
}

export interface SnippetRef {
    source: string;
    type?: 'file' | 'exam_question';
    name: string;
    startLine: number;
    endLine: number;
    preview?: string;
    content: string;
    isExamQuestion?: boolean;
    questionIndex?: number;
}

// ============== Tab Types ==============

export interface Tab {
    key: string;
    type: 'file' | 'images';
    name: string;
}

// ============== WebSocket Types ==============

export interface WSMessage {
    type: 'file_update' | 'notification' | 'pong';
    files?: string[];
    message?: string;
}

// ============== SSE Stream Types ==============

export interface StreamChunk {
    type: 'chunk' | 'status' | 'tool_start' | 'tool_result' | 'code' | 'output' | 'checkpoint' | 'done' | 'error' | 'sources';
    content?: string;
    success?: boolean;
}

// ============== API Response Types ==============

export interface ApiResponse<T = unknown> {
    success?: boolean;
    error?: string;
    data?: T;
}

export interface AskResponse {
    answer: string;
    tools_used?: string[];
    checkpoint?: string;
}

export interface RagSource {
    source: string;
    chunk_index: number;
    score?: number;
    content?: string;
}
