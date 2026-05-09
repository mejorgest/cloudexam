/**
 * A2UIImageGallery Component
 * Renders medical images from A2UI components in exam justifications.
 * Follows A2UI protocol for declarative UI rendering.
 * Supports deep RAG-based image refinement.
 */

import { useState, useEffect, useCallback, useRef, memo } from 'react';
import { enrichJustificationWithImages, refineImages, type A2UIComponent } from '../../services/api';
import { Image, X, ZoomIn, Info, RefreshCw, Sparkles } from 'lucide-react';
import './A2UIImageGallery.css';

export interface A2UIImageGalleryProps {
    questionText: string;
    justificationText: string;
    questionIndex: number;
    onImagesLoaded?: (count: number) => void;
}

// Extended component with optional metadata from refine
interface A2UIComponentExt extends A2UIComponent {
    metadata?: {
        image_id: number;
        rag_score: number;
        llm_score?: number;
        final_score?: number;
        is_new: boolean;
        matched_keywords: string[];
        all_keywords: string[];
        description?: string;
        llm_reason?: string;
    };
}

// Image viewer modal for full-size viewing
const ImageModal = memo(({
    imageUrl,
    caption,
    onClose
}: {
    imageUrl: string;
    caption: string;
    onClose: () => void;
}) => {
    useEffect(() => {
        const handleEsc = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', handleEsc);
        return () => window.removeEventListener('keydown', handleEsc);
    }, [onClose]);

    return (
        <div className="a2ui-modal-overlay" onClick={onClose}>
            <div className="a2ui-modal-content" onClick={(e) => e.stopPropagation()}>
                <button className="a2ui-modal-close" onClick={onClose}>
                    <X size={20} />
                </button>
                <img src={imageUrl} alt={caption} className="a2ui-modal-image" />
                {caption && <div className="a2ui-modal-caption">{caption}</div>}
            </div>
        </div>
    );
});

ImageModal.displayName = 'ImageModal';

// Single A2UI Image component with optional "new" badge
const A2UIImage = memo(({
    component,
    onClick,
    isNew
}: {
    component: A2UIComponentExt;
    onClick: (url: string, caption: string) => void;
    isNew?: boolean;
}) => {
    const { url, alt, caption, category } = component.properties;
    const [loaded, setLoaded] = useState(false);
    const [error, setError] = useState(false);

    if (error) {
        return (
            <div className="a2ui-image-error">
                <Image size={24} />
                <span>Error cargando imagen</span>
            </div>
        );
    }

    return (
        <div className={`a2ui-image-container ${loaded ? 'loaded' : 'loading'} ${isNew ? 'is-new' : ''}`}>
            {!loaded && (
                <div className="a2ui-image-skeleton">
                    <Image size={32} />
                </div>
            )}
            {isNew && loaded && (
                <div className="a2ui-new-badge">
                    <Sparkles size={10} /> NUEVA
                </div>
            )}
            <img
                src={url}
                alt={alt || caption || 'Imagen médica'}
                className="a2ui-image"
                onLoad={() => setLoaded(true)}
                onError={() => setError(true)}
                onClick={() => onClick(url, caption || alt || '')}
            />
            {loaded && (
                <div className="a2ui-image-overlay">
                    <button
                        className="a2ui-zoom-btn"
                        onClick={() => onClick(url, caption || alt || '')}
                        title="Ver imagen completa"
                    >
                        <ZoomIn size={16} />
                    </button>
                </div>
            )}
            {loaded && caption && (
                <div className="a2ui-image-caption">
                    {category && <span className="a2ui-image-category">{category}</span>}
                    {caption}
                </div>
            )}
            {loaded && component.metadata?.final_score ? (
                <div className="a2ui-rag-score" title={
                    `Score: ${(component.metadata.final_score * 100).toFixed(0)}% ` +
                    (component.metadata.llm_score ? `(LLM: ${(component.metadata.llm_score * 100).toFixed(0)}%, RAG: ${(component.metadata.rag_score * 100).toFixed(0)}%)` : `(RAG: ${(component.metadata.rag_score * 100).toFixed(0)}%)`)
                }>
                    {component.metadata.llm_score ? '🧠' : ''} {(component.metadata.final_score * 100).toFixed(0)}%
                </div>
            ) : loaded && component.metadata?.rag_score ? (
                <div className="a2ui-rag-score" title={`Relevancia RAG: ${(component.metadata.rag_score * 100).toFixed(0)}%`}>
                    {(component.metadata.rag_score * 100).toFixed(0)}%
                </div>
            ) : null}
        </div>
    );
});

A2UIImage.displayName = 'A2UIImage';

// Main gallery component
export const A2UIImageGallery = memo(({
    questionText,
    justificationText,
    questionIndex: _questionIndex,
    onImagesLoaded
}: A2UIImageGalleryProps) => {
    const [components, setComponents] = useState<A2UIComponentExt[]>([]);
    const [keywords, setKeywords] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);
    const [refining, setRefining] = useState(false);
    const [refined, setRefined] = useState(false);
    const [refineStats, setRefineStats] = useState<{ newCount: number; total: number } | null>(null);
    const [modalImage, setModalImage] = useState<{ url: string; caption: string } | null>(null);
    const [expanded, setExpanded] = useState(true);

    // Keep latest text in refs (no re-renders, no re-fetches)
    const justificationRef = useRef(justificationText);
    const questionRef = useRef(questionText);
    justificationRef.current = justificationText;
    questionRef.current = questionText;

    // Track if we've already fetched for this component instance
    const hasFetchedRef = useRef(false);

    // Fetch images ONCE on mount (or when text first appears with enough content)
    // NEVER re-fetch on text edits/saves - only via Refine button
    useEffect(() => {
        // Already fetched for this component - do nothing
        if (hasFetchedRef.current) return;

        if (!justificationText || justificationText.length < 50) {
            return; // Not enough text yet, wait
        }

        hasFetchedRef.current = true;

        const fetchImages = async () => {
            setLoading(true);
            try {
                console.log('🖼️ [Gallery] Initial image fetch for text length:', justificationText.length);
                const result = await enrichJustificationWithImages(justificationText, questionText);
                if (result.success && result.a2ui_components.length > 0) {
                    setComponents(result.a2ui_components);
                    setKeywords(result.keywords_detected);
                    onImagesLoaded?.(result.images_found);
                    console.log('🖼️ [Gallery] Loaded', result.images_found, 'images');
                }
            } catch (error) {
                console.warn('🖼️ [Gallery] Failed to fetch images:', error);
            } finally {
                setLoading(false);
            }
        };

        // Small delay on first render
        const timeout = setTimeout(fetchImages, 1200);
        return () => clearTimeout(timeout);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [justificationText]); // dep still needed to detect when text first becomes long enough

    // Refine images using deep RAG search
    const handleRefine = useCallback(async () => {
        setRefining(true);
        try {
            // Get current image IDs from displayed components
            const currentIds = components
                .map(c => {
                    // Extract ID from url like /api/medical-images/5
                    const match = c.properties.url.match(/\/api\/medical-images\/(\d+)/);
                    return match ? parseInt(match[1]) : null;
                })
                .filter((id): id is number => id !== null);

            const result = await refineImages(questionRef.current, justificationRef.current, currentIds);

            if (result.success && result.a2ui_components.length > 0) {
                setComponents(result.a2ui_components as A2UIComponentExt[]);
                setKeywords(result.keywords_detected);
                setRefined(true);
                setRefineStats({
                    newCount: result.new_images,
                    total: result.images_found,
                });
                setExpanded(true);
                console.log('🔬 [Gallery] Refined:', result.images_found, 'images,', result.new_images, 'new');
            }
        } catch (error) {
            console.warn('🔬 [Gallery] Failed to refine images:', error);
        } finally {
            setRefining(false);
        }
    }, [components]); // Only depends on components for extracting current IDs

    const handleImageClick = useCallback((url: string, caption: string) => {
        setModalImage({ url, caption });
    }, []);

    const handleCloseModal = useCallback(() => {
        setModalImage(null);
    }, []);

    // Don't render if no images found
    if (components.length === 0 && !loading) {
        return null;
    }

    return (
        <div className={`a2ui-gallery-wrapper ${refined ? 'refined' : ''}`}>
            {/* Header with toggle */}
            <div className="a2ui-gallery-header">
                <div className="a2ui-gallery-header-left" onClick={() => setExpanded(!expanded)}>
                    <span className="a2ui-gallery-icon">{refined ? '🔬' : '🖼️'}</span>
                    <span className="a2ui-gallery-title">
                        {refined ? 'Imágenes refinadas' : 'Imágenes relacionadas'} ({components.length})
                    </span>
                    {keywords.length > 0 && (
                        <span className="a2ui-keywords-hint" title={keywords.join(', ')}>
                            <Info size={12} /> {keywords.slice(0, 3).join(', ')}
                            {keywords.length > 3 && '...'}
                        </span>
                    )}
                    <span className={`a2ui-toggle ${expanded ? 'expanded' : ''}`}>▼</span>
                </div>
                {/* Refine button */}
                <button
                    className={`a2ui-refine-btn ${refining ? 'refining' : ''} ${refined ? 'refined' : ''}`}
                    onClick={(e) => { e.stopPropagation(); handleRefine(); }}
                    disabled={refining || loading}
                    title="Buscar imágenes más relevantes usando RAG profundo"
                >
                    {refining ? (
                        <><RefreshCw size={12} className="a2ui-spin" /> Refinando...</>
                    ) : refined ? (
                        <><Sparkles size={12} /> Re-refinar</>
                    ) : (
                        <><RefreshCw size={12} /> Refinar</>
                    )}
                </button>
            </div>

            {/* Refine stats */}
            {refined && refineStats && (
                <div className="a2ui-refine-stats">
                    🔬 {refineStats.total} imágenes encontradas
                    {refineStats.newCount > 0 && (
                        <span className="a2ui-new-count"> • {refineStats.newCount} nuevas</span>
                    )}
                </div>
            )}

            {/* Image grid */}
            {expanded && (
                <div className="a2ui-gallery-content">
                    {(loading || refining) ? (
                        <div className="a2ui-gallery-loading">
                            <div className="a2ui-loading-spinner"></div>
                            <span>{refining ? 'Refinando con RAG profundo...' : 'Buscando imágenes...'}</span>
                        </div>
                    ) : (
                        <div className="a2ui-image-grid">
                            {components.map((component) => (
                                <A2UIImage
                                    key={component.id}
                                    component={component}
                                    onClick={handleImageClick}
                                    isNew={(component as A2UIComponentExt).metadata?.is_new}
                                />
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Modal for full-size image */}
            {modalImage && (
                <ImageModal
                    imageUrl={modalImage.url}
                    caption={modalImage.caption}
                    onClose={handleCloseModal}
                />
            )}
        </div>
    );
});

A2UIImageGallery.displayName = 'A2UIImageGallery';

export default A2UIImageGallery;
