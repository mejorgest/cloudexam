import { useEffect, useCallback, useRef } from 'react';
import { useAppStore } from '../store/appStore';
import { fetchState, fetchFiles, fetchContextInfo, fetchChangelog } from '../services/api';

// Longer polling intervals for better performance
const DATA_POLL_INTERVAL = 15000; // 15 seconds - rely on WebSocket for real-time
const CONTEXT_POLL_INTERVAL = 30000; // 30 seconds
const DEBUG_POLL_INTERVAL = 5000; // 5 seconds only when open

export function useDataPolling() {
    const {
        setState,
        setFiles,
        setContextInfo,
        setChangelog,
        isEditMode,
        isExamEditing,
        debugOpen,
        addPendingChange,
        currentAnalyzingIndex,
    } = useAppStore();

    const previousStateRef = useRef<string>('');
    const previousFilesRef = useRef<string>('');
    const isMountedRef = useRef(true);

    // Use refs to always read the LATEST values in callbacks
    // This prevents the stale closure bug where interval callbacks
    // capture old values and continue polling during editing
    const isEditModeRef = useRef(isEditMode);
    const isExamEditingRef = useRef(isExamEditing);
    const currentAnalyzingIndexRef = useRef(currentAnalyzingIndex);
    isEditModeRef.current = isEditMode;
    isExamEditingRef.current = isExamEditing;
    currentAnalyzingIndexRef.current = currentAnalyzingIndex;

    // Data fetch function - uses refs to always check latest editing state
    const updateData = useCallback(async () => {
        // Skip polling during ANY kind of editing or while analyzing a question
        if (!isMountedRef.current || isEditModeRef.current || isExamEditingRef.current || currentAnalyzingIndexRef.current !== null) {
            console.log('⏸️ [Polling] Skipped - editing:', isExamEditingRef.current, 'editMode:', isEditModeRef.current, 'analyzing:', currentAnalyzingIndexRef.current);
            return;
        }

        try {
            // Fetch state
            const stateData = await fetchState();
            const newState = stateData.state || {};
            const newStateJson = JSON.stringify(newState);

            // Double-check editing state AFTER await (user may have started editing during fetch)
            if (isExamEditingRef.current || isEditModeRef.current) {
                console.log('⏸️ [Polling] Discarding fetch result - editing started during fetch');
                return;
            }

            // Only update if actually changed
            if (newStateJson !== previousStateRef.current) {
                const oldState = previousStateRef.current ? JSON.parse(previousStateRef.current) : {};

                // Find modified states
                for (const key of Object.keys(newState)) {
                    if (key.startsWith('_')) continue;
                    if (oldState[key] !== undefined && newState[key] !== oldState[key]) {
                        addPendingChange(key);
                    }
                }

                previousStateRef.current = newStateJson;
                setState(newState);
            }

            // Fetch files
            const filesData = await fetchFiles();
            const newFiles = filesData.files || [];
            const newFilesJson = JSON.stringify(newFiles);

            if (newFilesJson !== previousFilesRef.current) {
                previousFilesRef.current = newFilesJson;
                setFiles(newFiles.map((f: string) => ({ name: f })));
            }
        } catch (error) {
            console.error('Error updating data:', error);
        }
    }, [setState, setFiles, addPendingChange]); // No isEditMode/isExamEditing - use refs instead

    // Context info fetch
    const updateContextInfo = useCallback(async () => {
        if (!isMountedRef.current) return;

        try {
            const data = await fetchContextInfo();
            if (!('error' in data)) {
                setContextInfo(data);
            }
        } catch {
            // Silently fail - not critical
        }
    }, [setContextInfo]);

    // Changelog fetch
    const updateChangelog = useCallback(async () => {
        if (!isMountedRef.current || !debugOpen) return;

        try {
            const data = await fetchChangelog();
            setChangelog(data.changes || []);
        } catch (error) {
            console.error('Error fetching changelog:', error);
        }
    }, [debugOpen, setChangelog]);

    // Initial load - only once
    useEffect(() => {
        isMountedRef.current = true;

        // Stagger initial loads to prevent API spike
        updateData();
        setTimeout(updateContextInfo, 1000);

        return () => {
            isMountedRef.current = false;
        };
    }, []); // Empty deps - run only once on mount

    // Polling intervals - with stable refs
    useEffect(() => {
        const dataInterval = setInterval(updateData, DATA_POLL_INTERVAL);
        const contextInterval = setInterval(updateContextInfo, CONTEXT_POLL_INTERVAL);

        return () => {
            clearInterval(dataInterval);
            clearInterval(contextInterval);
        };
    }, [updateData, updateContextInfo]);

    // Debug log polling - only when open
    useEffect(() => {
        if (debugOpen) {
            updateChangelog();
            const debugInterval = setInterval(updateChangelog, DEBUG_POLL_INTERVAL);
            return () => clearInterval(debugInterval);
        }
    }, [debugOpen, updateChangelog]);

    return { updateData, updateContextInfo };
}
