import type {
    StateData,
    PdfDocument,
    ContextInfo,
    ChangelogEntry,
    AskResponse,
    DiffData,
    ExamQuestion,
} from '../types';

const API_BASE = '';  // Same origin

// ============== State API ==============

export async function fetchState(): Promise<{ state: StateData }> {
    const response = await fetch(`${API_BASE}/api/workspace/state`);
    if (!response.ok) throw new Error('Failed to fetch state');
    return response.json();
}

export async function saveState(key: string, value: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/workspace/state`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
    });
    if (!response.ok) throw new Error('Failed to save state');
}

export async function deleteState(key: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/workspace/state/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key }),
    });
    if (!response.ok) throw new Error('Failed to delete state');
}

// ============== Files API ==============

export async function fetchFiles(): Promise<{ files: string[] }> {
    const response = await fetch(`${API_BASE}/api/workspace/files`);
    if (!response.ok) throw new Error('Failed to fetch files');
    return response.json();
}

export async function readFile(filename: string): Promise<{ content: string }> {
    const response = await fetch(`${API_BASE}/api/workspace/files/read`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename }),
    });
    if (!response.ok) throw new Error('Failed to read file');
    return response.json();
}

export async function writeFile(filename: string, content: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/workspace/files/write`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename, content }),
    });
    if (!response.ok) throw new Error('Failed to write file');
}

export async function deleteFile(filename: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/workspace/files/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename }),
    });
    if (!response.ok) throw new Error('Failed to delete file');
}

// ============== PDF API ==============

export async function fetchPdfDocuments(): Promise<{ success: boolean; documents: PdfDocument[] }> {
    const response = await fetch(`${API_BASE}/api/pdf/documents`);
    if (!response.ok) throw new Error('Failed to fetch PDF documents');
    return response.json();
}

export async function deletePdfDocument(docId: string): Promise<{ success: boolean; chunks_deleted?: number; error?: string }> {
    const response = await fetch(`${API_BASE}/api/pdf/documents/${docId}`, {
        method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete PDF document');
    return response.json();
}

export async function uploadPdf(file: File): Promise<{
    success: boolean;
    pages?: number;
    saved_to?: string;
    doc_id?: string;
    filename?: string;
    workspace_file?: string;
    state_key?: string;
    error?: string;
}> {
    const formData = new FormData();
    formData.append('pdf', file);

    const response = await fetch(`${API_BASE}/api/upload/pdf`, {
        method: 'POST',
        body: formData,
    });
    if (!response.ok) throw new Error('Failed to upload PDF');
    return response.json();
}

export async function extractExamFromPdf(file: File, outputName: string): Promise<{
    success: boolean;
    filename?: string;
    total_questions?: number;
    pages_processed?: number;
    error?: string;
}> {
    const formData = new FormData();
    formData.append('pdf', file);
    formData.append('output_name', outputName);

    const response = await fetch(`${API_BASE}/api/exams/extract-from-pdf`, {
        method: 'POST',
        body: formData,
    });
    if (!response.ok) throw new Error('Failed to extract exam');
    return response.json();
}

// ============== Chat API ==============

export interface AskRequest {
    question: string;
    thread_id?: string;
    context_files?: { name: string; content: string }[];
}

export async function askAgent(request: AskRequest): Promise<AskResponse> {
    const response = await fetch(`${API_BASE}/api/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });
    if (!response.ok) throw new Error('Failed to ask agent');
    return response.json();
}

export async function* askAgentStream(request: AskRequest): AsyncGenerator<{
    type: string;
    content?: string;
    success?: boolean;
}> {
    const response = await fetch(`${API_BASE}/api/ask/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });

    if (!response.ok) throw new Error('Failed to start stream');
    if (!response.body) throw new Error('No response body');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                try {
                    const data = JSON.parse(line.slice(6));
                    yield data;
                } catch (e) {
                    console.warn('Failed to parse SSE data:', line, e);
                }
            }
        }
    }

    // Process any remaining data in buffer after stream ends
    if (buffer.trim()) {
        const remainingLines = buffer.split('\n');
        for (const line of remainingLines) {
            if (line.startsWith('data: ')) {
                try {
                    const data = JSON.parse(line.slice(6));
                    yield data;
                } catch (e) {
                    console.warn('Failed to parse remaining SSE data:', line, e);
                }
            }
        }
    }
}

// ============== RAG API ==============

export interface RagSearchRequest {
    query: string;
    num_results?: number;
}

export async function* ragSearchStream(request: RagSearchRequest): AsyncGenerator<{
    type: string;
    content?: unknown;
}> {
    const response = await fetch(`${API_BASE}/api/rag/search/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });

    if (!response.ok) throw new Error('Failed to start RAG search');
    if (!response.body) throw new Error('No response body');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                try {
                    const data = JSON.parse(line.slice(6));
                    yield data;
                } catch (e) {
                    console.warn('Failed to parse RAG SSE data:', line, e);
                }
            }
        }
    }
}

// ============== Context API ==============

export async function fetchContextInfo(): Promise<ContextInfo> {
    const response = await fetch(`${API_BASE}/api/context-info`);
    if (!response.ok) throw new Error('Failed to fetch context info');
    return response.json();
}

// ============== Debug API ==============

export async function fetchChangelog(): Promise<{ changes: ChangelogEntry[] }> {
    const response = await fetch(`${API_BASE}/api/debug/changelog`);
    if (!response.ok) throw new Error('Failed to fetch changelog');
    return response.json();
}

// ============== Diff API ==============

export async function fetchDiff(stateKey: string): Promise<DiffData> {
    const response = await fetch(`${API_BASE}/api/diff/${encodeURIComponent(stateKey)}`);
    if (!response.ok) throw new Error('Failed to fetch diff');
    return response.json();
}

// ============== Checkpoint API ==============

export async function createCheckpoint(message: string): Promise<{ success: boolean; hash?: string; error?: string }> {
    const response = await fetch(`${API_BASE}/api/checkpoints/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
    });
    if (!response.ok) throw new Error('Failed to create checkpoint');
    return response.json();
}

export async function restoreCheckpoint(hash: string): Promise<{ success: boolean; error?: string }> {
    const response = await fetch(`${API_BASE}/api/checkpoints/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hash }),
    });
    if (!response.ok) throw new Error('Failed to restore checkpoint');
    return response.json();
}

// ============== Exam Save API (custom since it modifies workspace files) ==============

export async function saveExamQuestions(filename: string, questions: ExamQuestion[], originalData?: string): Promise<void> {
    let content: string;

    // Preserve original structure if it was a wrapped format
    if (originalData) {
        try {
            const original = JSON.parse(originalData);
            if (!Array.isArray(original) && original.preguntas) {
                // Wrapped format - preserve structure
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

    await writeFile(filename, content);
}

// ============== Medical Images API (A2UI Support) ==============

export interface MedicalImage {
    id: number;
    filename: string;
    keywords: string[];
    category: string;
    title: string;
    description: string;
    match_count?: number;  // For search results
}

export interface A2UIComponent {
    type: 'Image' | 'Card' | 'Gallery';
    id: string;
    properties: {
        url: string;
        alt?: string;
        caption?: string;
        category?: string;
    };
}

export interface EnrichResult {
    success: boolean;
    text: string;
    a2ui_components: A2UIComponent[];
    keywords_detected: string[];
    images_found: number;
}

export async function fetchMedicalImages(): Promise<{ success: boolean; images: MedicalImage[]; count: number }> {
    const response = await fetch(`${API_BASE}/api/medical-images`);
    if (!response.ok) throw new Error('Failed to fetch medical images');
    return response.json();
}

export async function searchMedicalImages(keywords: string[], limit = 5): Promise<{
    success: boolean;
    images: MedicalImage[];
    count: number;
    searched_keywords: string[];
}> {
    const response = await fetch(`${API_BASE}/api/medical-images/search?keywords=${encodeURIComponent(keywords.join(','))}&limit=${limit}`);
    if (!response.ok) throw new Error('Failed to search medical images');
    return response.json();
}

export async function uploadMedicalImage(
    file: File,
    keywords: string[],
    title = '',
    description = '',
    category = 'general'
): Promise<{ success: boolean; id?: number; filename?: string; keywords?: string[]; error?: string }> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('keywords', keywords.join(','));
    formData.append('title', title);
    formData.append('description', description);
    formData.append('category', category);

    const response = await fetch(`${API_BASE}/api/medical-images/upload`, {
        method: 'POST',
        body: formData,
    });
    if (!response.ok) throw new Error('Failed to upload medical image');
    return response.json();
}

export async function deleteMedicalImage(imageId: number): Promise<{ success: boolean }> {
    const response = await fetch(`${API_BASE}/api/medical-images/${imageId}`, {
        method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete medical image');
    return response.json();
}

export async function enrichJustificationWithImages(
    justification: string,
    question: string = ''
): Promise<EnrichResult> {
    const response = await fetch(`${API_BASE}/api/medical-images/enrich`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ justification, question }),
    });
    if (!response.ok) throw new Error('Failed to enrich with images');
    return response.json();
}

export interface RefineResult extends EnrichResult {
    new_images: number;
    total_candidates: number;
    message?: string;
}

export async function refineImages(
    questionText: string,
    justificationText: string,
    currentImageIds: number[] = []
): Promise<RefineResult> {
    const response = await fetch(`${API_BASE}/api/medical-images/refine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            question_text: questionText,
            justification_text: justificationText,
            current_image_ids: currentImageIds,
        }),
    });
    if (!response.ok) throw new Error('Failed to refine images');
    return response.json();
}

/**
 * Get the URL for serving a medical image by ID.
 * Use this in <img> src attributes.
 */
export function getMedicalImageUrl(imageId: number): string {
    return `${API_BASE}/api/medical-images/${imageId}`;
}

