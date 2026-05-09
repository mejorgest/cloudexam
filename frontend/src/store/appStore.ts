import { create } from 'zustand';
import type {
    WorkspaceFile,
    Tab,
    AttachedFile,
    SnippetRef,
    ChatMessage,
    ContextInfo,
    ChangelogEntry,
} from '../types';

interface PendingExamAnalysis {
    examKey: string | null;
    examData: string | null;
    originalExamData: string | null;
    questionIndex: number | null;
}

interface AppState {
    // Data
    files: WorkspaceFile[];
    contextInfo: ContextInfo | null;
    changelog: ChangelogEntry[];

    // UI State
    selectedKey: string | null;
    openTabs: Tab[];
    tabScrollPositions: Record<string, { type: 'exam' | 'content'; scrollTop: number }>;
    isEditMode: boolean;
    isExamEditing: boolean;
    isLoading: boolean;
    debugOpen: boolean;
    a2uiImagesEnabled: boolean;
    sidebarOpen: boolean;
    chatOpen: boolean;

    // Chat
    messages: ChatMessage[];
    attachedFiles: AttachedFile[];
    snippetRefs: SnippetRef[];
    currentAnalyzingIndex: number | null;
    analysisMode: 'append' | 'overwrite' | null;
    pendingExamAnalysis: PendingExamAnalysis;
    useStreaming: boolean;
    chatInputValue: string;
    shouldTriggerSend: boolean;
    streamingJustification: string;
    lastAnalyzedIndex: number | null;

    // Actions - Data
    setFiles: (files: WorkspaceFile[]) => void;
    setContextInfo: (info: ContextInfo | null) => void;
    setChangelog: (entries: ChangelogEntry[]) => void;

    // Actions - UI
    setSelectedKey: (key: string | null) => void;
    openTab: (tab: Tab) => void;
    closeTab: (key: string) => void;
    switchToTab: (key: string) => void;
    setTabScrollPosition: (key: string, position: { type: 'exam' | 'content'; scrollTop: number }) => void;
    setIsEditMode: (mode: boolean) => void;
    setIsExamEditing: (editing: boolean) => void;
    setIsLoading: (loading: boolean) => void;
    toggleDebug: () => void;
    toggleA2UIImages: () => void;
    toggleSidebar: () => void;
    setSidebarOpen: (open: boolean) => void;
    toggleChat: () => void;
    setChatOpen: (open: boolean) => void;

    // Actions - Chat
    addMessage: (message: ChatMessage) => void;
    updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
    clearMessages: () => void;
    addAttachedFile: (file: AttachedFile) => void;
    removeAttachedFile: (index: number) => void;
    clearAttachedFiles: () => void;
    addSnippetRef: (ref: SnippetRef) => void;
    removeSnippetRef: (index: number) => void;
    clearSnippetRefs: () => void;
    setCurrentAnalyzingIndex: (index: number | null) => void;
    setAnalysisMode: (mode: 'append' | 'overwrite' | null) => void;
    setPendingExamAnalysis: (analysis: PendingExamAnalysis) => void;
    setChatInputValue: (value: string) => void;
    triggerSend: () => void;
    resetTriggerSend: () => void;
    setStreamingJustification: (content: string) => void;
    setLastAnalyzedIndex: (index: number | null) => void;
}

export const useAppStore = create<AppState>((set, get) => ({
    files: [],
    contextInfo: null,
    changelog: [],

    selectedKey: null,
    openTabs: [],
    tabScrollPositions: {},
    isEditMode: false,
    isExamEditing: false,
    isLoading: false,
    debugOpen: false,
    a2uiImagesEnabled: localStorage.getItem('a2uiImagesEnabled') === 'true',
    // On mobile, panels start hidden; on wider screens visible.
    sidebarOpen: typeof window !== 'undefined' ? window.innerWidth >= 768 : true,
    chatOpen: typeof window !== 'undefined' ? window.innerWidth >= 768 : true,

    messages: [{
        id: 'initial',
        type: 'system',
        content: 'Asistente de exámenes médicos. Adjunta un archivo con ➕ y pregúntame sobre él.',
        timestamp: new Date(),
    }],
    attachedFiles: [],
    snippetRefs: [],
    currentAnalyzingIndex: null,
    analysisMode: null,
    pendingExamAnalysis: {
        examKey: null,
        examData: null,
        originalExamData: null,
        questionIndex: null,
    },
    useStreaming: true,
    chatInputValue: '',
    shouldTriggerSend: false,
    streamingJustification: '',
    lastAnalyzedIndex: null,

    setFiles: (files) => set({ files }),
    setContextInfo: (contextInfo) => set({ contextInfo }),
    setChangelog: (changelog) => set({ changelog }),

    setSelectedKey: (selectedKey) => set({ selectedKey }),
    toggleA2UIImages: () => {
        const newVal = !get().a2uiImagesEnabled;
        localStorage.setItem('a2uiImagesEnabled', String(newVal));
        set({ a2uiImagesEnabled: newVal });
    },

    openTab: (tab) => {
        const { openTabs } = get();
        if (!openTabs.find(t => t.key === tab.key)) {
            set({ openTabs: [...openTabs, tab] });
        }
    },

    closeTab: (key) => {
        const { openTabs, selectedKey } = get();
        const newTabs = openTabs.filter(t => t.key !== key);
        set({ openTabs: newTabs });

        if (selectedKey === key && newTabs.length > 0) {
            const index = openTabs.findIndex(t => t.key === key);
            const newIndex = Math.max(0, index - 1);
            set({ selectedKey: newTabs[newIndex]?.key || null });
        } else if (newTabs.length === 0) {
            set({ selectedKey: null });
        }
    },

    switchToTab: (key) => {
        const { selectedKey, tabScrollPositions } = get();

        if (selectedKey) {
            const examContainer = document.querySelector('.exam-container');
            const contentDiv = document.getElementById('editorContent');

            if (examContainer && examContainer.scrollHeight > examContainer.clientHeight) {
                set({
                    tabScrollPositions: {
                        ...tabScrollPositions,
                        [selectedKey]: { type: 'exam', scrollTop: examContainer.scrollTop },
                    },
                });
            } else if (contentDiv) {
                set({
                    tabScrollPositions: {
                        ...tabScrollPositions,
                        [selectedKey]: { type: 'content', scrollTop: contentDiv.scrollTop },
                    },
                });
            }
        }

        set({ selectedKey: key });
    },

    setTabScrollPosition: (key, position) => {
        const { tabScrollPositions } = get();
        set({
            tabScrollPositions: {
                ...tabScrollPositions,
                [key]: position,
            },
        });
    },

    setIsEditMode: (isEditMode) => set({ isEditMode }),
    setIsExamEditing: (isExamEditing) => set({ isExamEditing }),
    setIsLoading: (isLoading) => set({ isLoading }),
    toggleDebug: () => set((state) => ({ debugOpen: !state.debugOpen })),
    toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
    setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
    toggleChat: () => set((state) => ({ chatOpen: !state.chatOpen })),
    setChatOpen: (chatOpen) => set({ chatOpen }),

    addMessage: (message) => set((state) => ({
        messages: [...state.messages, message],
    })),

    updateMessage: (id, updates) => set((state) => ({
        messages: state.messages.map(m =>
            m.id === id ? { ...m, ...updates } : m
        ),
    })),

    clearMessages: () => set({
        messages: [{
            id: 'initial',
            type: 'system',
            content: 'Asistente de exámenes médicos. Adjunta un archivo con ➕ y pregúntame sobre él.',
            timestamp: new Date(),
        }],
    }),

    addAttachedFile: (file) => set((state) => {
        const exists = state.attachedFiles.some(
            f => f.type === file.type && f.name === file.name
        );
        if (exists) return state;
        return { attachedFiles: [...state.attachedFiles, file] };
    }),

    removeAttachedFile: (index) => set((state) => ({
        attachedFiles: state.attachedFiles.filter((_, i) => i !== index),
    })),

    clearAttachedFiles: () => set({ attachedFiles: [] }),

    addSnippetRef: (ref) => set((state) => {
        const exists = state.snippetRefs.some(
            s => s.source === ref.source && s.startLine === ref.startLine && s.endLine === ref.endLine
        );
        if (exists) return state;
        return { snippetRefs: [...state.snippetRefs, ref] };
    }),

    removeSnippetRef: (index) => set((state) => ({
        snippetRefs: state.snippetRefs.filter((_, i) => i !== index),
    })),

    clearSnippetRefs: () => set({ snippetRefs: [] }),

    setCurrentAnalyzingIndex: (currentAnalyzingIndex) => set({ currentAnalyzingIndex }),
    setAnalysisMode: (analysisMode) => set({ analysisMode }),
    setChatInputValue: (chatInputValue) => set({ chatInputValue }),
    triggerSend: () => set({ shouldTriggerSend: true }),
    resetTriggerSend: () => set({ shouldTriggerSend: false }),
    setStreamingJustification: (streamingJustification) => set({ streamingJustification }),
    setLastAnalyzedIndex: (lastAnalyzedIndex) => set({ lastAnalyzedIndex }),
    setPendingExamAnalysis: (pendingExamAnalysis) => set({ pendingExamAnalysis }),
}));
