import { useEffect, useCallback, useRef } from 'react';
import { useAppStore } from '../store/appStore';
import { fetchFiles, fetchContextInfo, fetchChangelog } from '../services/api';

const DATA_POLL_INTERVAL = 15000; // 15 s
const CONTEXT_POLL_INTERVAL = 30000; // 30 s
const DEBUG_POLL_INTERVAL = 5000; // 5 s, sólo si está abierto

export function useDataPolling() {
    const {
        setFiles,
        setContextInfo,
        setChangelog,
        isEditMode,
        isExamEditing,
        debugOpen,
        currentAnalyzingIndex,
    } = useAppStore();

    const previousFilesRef = useRef<string>('');
    const isMountedRef = useRef(true);

    const isEditModeRef = useRef(isEditMode);
    const isExamEditingRef = useRef(isExamEditing);
    const currentAnalyzingIndexRef = useRef(currentAnalyzingIndex);
    isEditModeRef.current = isEditMode;
    isExamEditingRef.current = isExamEditing;
    currentAnalyzingIndexRef.current = currentAnalyzingIndex;

    const updateData = useCallback(async () => {
        if (
            !isMountedRef.current ||
            isEditModeRef.current ||
            isExamEditingRef.current ||
            currentAnalyzingIndexRef.current !== null
        ) {
            return;
        }

        try {
            const filesData = await fetchFiles();
            const newFiles = filesData.files || [];
            const newFilesJson = JSON.stringify(newFiles);

            if (newFilesJson !== previousFilesRef.current) {
                previousFilesRef.current = newFilesJson;
                setFiles(newFiles.map((f: string) => ({ name: f })));
            }
        } catch (error) {
            console.error('Error updating files:', error);
        }
    }, [setFiles]);

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

    const updateChangelog = useCallback(async () => {
        if (!isMountedRef.current || !debugOpen) return;
        try {
            const data = await fetchChangelog();
            setChangelog(data.changes || []);
        } catch (error) {
            console.error('Error fetching changelog:', error);
        }
    }, [debugOpen, setChangelog]);

    useEffect(() => {
        isMountedRef.current = true;
        updateData();
        setTimeout(updateContextInfo, 1000);
        return () => {
            isMountedRef.current = false;
        };
    }, []);

    useEffect(() => {
        const dataInterval = setInterval(updateData, DATA_POLL_INTERVAL);
        const contextInterval = setInterval(updateContextInfo, CONTEXT_POLL_INTERVAL);
        return () => {
            clearInterval(dataInterval);
            clearInterval(contextInterval);
        };
    }, [updateData, updateContextInfo]);

    useEffect(() => {
        if (debugOpen) {
            updateChangelog();
            const debugInterval = setInterval(updateChangelog, DEBUG_POLL_INTERVAL);
            return () => clearInterval(debugInterval);
        }
    }, [debugOpen, updateChangelog]);

    return { updateData, updateContextInfo };
}
