import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '../store/appStore';
import type { WSMessage } from '../types';

export function useWebSocket() {
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const { setState, setFiles } = useAppStore();

    const connect = useCallback(() => {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        try {
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onopen = () => {
                console.log('🔌 WebSocket connected');
            };

            ws.onmessage = (event) => {
                try {
                    const data: WSMessage = JSON.parse(event.data);

                    // Skip state updates during any kind of editing to prevent overwriting user changes
                    const store = useAppStore.getState();
                    const isUserEditing = store.isEditMode || store.currentAnalyzingIndex !== null || store.isExamEditing;

                    if (data.type === 'state_update' && data.state) {
                        if (!isUserEditing) {
                            setState(data.state);
                        }
                    }

                    if (data.type === 'file_update' && data.files) {
                        if (!isUserEditing) {
                            setFiles(data.files.map(f => ({ name: f })));
                        }
                    }

                    if (data.type === 'pong') {
                        // Heartbeat response
                    }
                } catch (e) {
                    console.warn('Failed to parse WebSocket message:', e);
                }
            };

            ws.onclose = () => {
                console.log('🔌 WebSocket disconnected, reconnecting in 3s...');
                wsRef.current = null;

                // Attempt reconnection
                reconnectTimeoutRef.current = setTimeout(() => {
                    connect();
                }, 3000);
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (e) {
            console.error('WebSocket init error:', e);

            // Fallback: will use polling
            reconnectTimeoutRef.current = setTimeout(() => {
                connect();
            }, 5000);
        }
    }, [setState, setFiles]); // WS handler reads editing state via getState() - no deps needed

    // Heartbeat ping
    useEffect(() => {
        const pingInterval = setInterval(() => {
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);

        return () => clearInterval(pingInterval);
    }, []);

    // Initial connection
    useEffect(() => {
        connect();

        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
            if (reconnectTimeoutRef.current) {
                clearTimeout(reconnectTimeoutRef.current);
            }
        };
    }, [connect]);

    return wsRef;
}
