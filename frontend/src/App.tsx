import { useEffect, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { EditorPanel } from './components/Editor';
import { ChatPanel } from './components/Chat';
import { DebugPanel } from './components/Debug';
import { ConfigScreen } from './components/ConfigScreen';
import { useWebSocket } from './hooks/useWebSocket';
import { useDataPolling } from './hooks/useDataPolling';
import { useAppStore } from './store/appStore';
import { Menu, MessageSquare } from 'lucide-react';
import './index.css';

type ConfigGate = 'checking' | 'needs_setup' | 'ready';

function App() {
    const [gate, setGate] = useState<ConfigGate>('checking');
    const [showSettings, setShowSettings] = useState(false);

    // Probe config on mount; if not ready, show blocking config screen.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const r = await fetch('/api/config/status');
                const data = await r.json();
                if (cancelled) return;
                setGate(data.needs_setup ? 'needs_setup' : 'ready');
            } catch {
                if (!cancelled) setGate('needs_setup');
            }
        })();
        return () => { cancelled = true; };
    }, []);

    if (gate === 'checking') {
        return (
            <div className="config-screen blocking">
                <div className="config-card"><p>Comprobando configuración…</p></div>
            </div>
        );
    }

    if (gate === 'needs_setup') {
        return <ConfigScreen blocking onConfigured={() => setGate('ready')} />;
    }

    return <Main onOpenSettings={() => setShowSettings(true)} settingsOpen={showSettings} onCloseSettings={() => setShowSettings(false)} />;
}

interface MainProps {
    onOpenSettings: () => void;
    settingsOpen: boolean;
    onCloseSettings: () => void;
}

function Main({ onOpenSettings, settingsOpen, onCloseSettings }: MainProps) {
    useWebSocket();
    useDataPolling();
    const { sidebarOpen, chatOpen, toggleSidebar, toggleChat, setSidebarOpen, setChatOpen } = useAppStore();

    return (
        <div className={`app-container ${sidebarOpen ? 'sidebar-open' : ''} ${chatOpen ? 'chat-open' : ''}`}>
            <Sidebar />
            <EditorPanel />
            <ChatPanel />
            <DebugPanel />

            {/* Mobile drawer backdrop — closes the open drawer when tapped */}
            {(sidebarOpen || chatOpen) && (
                <div
                    className="mobile-backdrop"
                    onClick={() => { setSidebarOpen(false); setChatOpen(false); }}
                    aria-hidden
                />
            )}

            {/* Mobile FABs to toggle drawers — hidden on desktop via CSS */}
            <button
                className="drawer-fab drawer-fab-left"
                onClick={toggleSidebar}
                title="Mostrar/ocultar archivos"
                aria-label="Toggle sidebar"
            >
                <Menu size={20} />
            </button>
            <button
                className="drawer-fab drawer-fab-right"
                onClick={toggleChat}
                title="Mostrar/ocultar chat"
                aria-label="Toggle chat"
            >
                <MessageSquare size={20} />
            </button>

            {/* Settings access — always available from main UI */}
            <button
                className="config-fab"
                onClick={onOpenSettings}
                title="Configuración (API keys)"
                aria-label="Configuración"
            >
                ⚙️
            </button>

            {settingsOpen && (
                <ConfigScreen blocking={false} onClose={onCloseSettings} />
            )}
        </div>
    );
}

export default App;
