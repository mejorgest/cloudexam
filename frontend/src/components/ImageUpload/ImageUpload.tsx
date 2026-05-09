import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Upload, X, Check, Image, Tag, FolderOpen, FileText, Loader2, ExternalLink, Trash2, AlertCircle, Search, ChevronLeft, ChevronRight, Edit3, Save } from 'lucide-react';

const API_BASE = '';

interface MedicalImage {
    id: number;
    title: string;
    keywords: string[];
    category: string;
    description?: string;
    filename: string;
}

interface UploadResponse {
    success: boolean;
    id?: number;
    title?: string;
    keywords?: string[];
    category?: string;
    http_url?: string;
    embeddings_generated?: boolean;
    error?: string;
}

const CATEGORIES = [
    { value: 'hematología', label: '🩸 Hematología' },
    { value: 'cardiología', label: '❤️ Cardiología' },
    { value: 'neurología', label: '🧠 Neurología' },
    { value: 'neumología', label: '🫁 Neumología' },
    { value: 'anatomía', label: '🦴 Anatomía' },
    { value: 'patología', label: '🔬 Patología' },
    { value: 'radiología', label: '📷 Radiología' },
    { value: 'microbiología', label: '🦠 Microbiología' },
    { value: 'farmacología', label: '💊 Farmacología' },
    { value: 'general', label: '📋 General' },
];

export function ImageUpload() {
    // Form state
    const [file, setFile] = useState<File | null>(null);
    const [preview, setPreview] = useState<string>('');
    const [title, setTitle] = useState('');
    const [keywords, setKeywords] = useState('');
    const [category, setCategory] = useState('hematología');
    const [description, setDescription] = useState('');

    // UI state
    const [uploading, setUploading] = useState(false);
    const [result, setResult] = useState<UploadResponse | null>(null);
    const [images, setImages] = useState<MedicalImage[]>([]);
    const [dragActive, setDragActive] = useState(false);

    // Edit modal state
    const [editImage, setEditImage] = useState<MedicalImage | null>(null);
    const [editTitle, setEditTitle] = useState('');
    const [editKeywords, setEditKeywords] = useState('');
    const [editCategory, setEditCategory] = useState('');
    const [editDescription, setEditDescription] = useState('');
    const [editSaving, setEditSaving] = useState(false);
    const [editResult, setEditResult] = useState<{ success: boolean; message: string } | null>(null);

    // Search & Pagination
    const [searchQuery, setSearchQuery] = useState('');
    const [currentPage, setCurrentPage] = useState(1);
    const PAGE_SIZE = 20;

    // Filtered images by search
    const filteredImages = useMemo(() => {
        if (!searchQuery.trim()) return images;
        const q = searchQuery.toLowerCase();
        return images.filter(img =>
            img.title?.toLowerCase().includes(q) ||
            img.category?.toLowerCase().includes(q) ||
            img.keywords?.some(kw => kw.toLowerCase().includes(q))
        );
    }, [images, searchQuery]);

    // Paginated images
    const totalPages = Math.max(1, Math.ceil(filteredImages.length / PAGE_SIZE));
    const paginatedImages = useMemo(() => {
        const start = (currentPage - 1) * PAGE_SIZE;
        return filteredImages.slice(start, start + PAGE_SIZE);
    }, [filteredImages, currentPage]);

    // Reset page when search changes
    useEffect(() => { setCurrentPage(1); }, [searchQuery]);

    const fileInputRef = useRef<HTMLInputElement>(null);

    // Load existing images
    const loadImages = useCallback(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/medical-images`);
            const data = await res.json();
            if (data.images) setImages(data.images);
        } catch (e) {
            console.error('Error loading images:', e);
        }
    }, []);

    useEffect(() => { loadImages(); }, [loadImages]);

    // Handle file selection
    const handleFile = useCallback((f: File) => {
        setFile(f);
        const reader = new FileReader();
        reader.onload = (e) => setPreview(e.target?.result as string);
        reader.readAsDataURL(f);

        // Auto-generate title from filename
        if (!title) {
            setTitle(f.name.replace(/\.[^.]+$/, '').replace(/[_-]/g, ' '));
        }
    }, [title]);

    // Drag & drop handlers
    const handleDrag = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.type === 'dragenter' || e.type === 'dragover') setDragActive(true);
        else if (e.type === 'dragleave') setDragActive(false);
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);
        if (e.dataTransfer.files?.[0]) handleFile(e.dataTransfer.files[0]);
    }, [handleFile]);

    // Submit upload
    const handleSubmit = useCallback(async () => {
        if (!file || !keywords.trim()) return;

        setUploading(true);
        setResult(null);

        const formData = new FormData();
        formData.append('file', file);
        formData.append('title', title);
        formData.append('keywords', keywords);
        formData.append('category', category);
        formData.append('description', description);

        try {
            const res = await fetch(`${API_BASE}/api/subir-imagen`, {
                method: 'POST',
                body: formData,
            });
            const data: UploadResponse = await res.json();
            setResult(data);

            if (data.success) {
                setFile(null);
                setPreview('');
                setTitle('');
                setKeywords('');
                setDescription('');
                loadImages();
            }
        } catch (e) {
            setResult({ success: false, error: String(e) });
        } finally {
            setUploading(false);
        }
    }, [file, title, keywords, category, description, loadImages]);

    // Delete image
    const handleDelete = useCallback(async (id: number) => {
        if (!window.confirm('¿Eliminar esta imagen?')) return;
        try {
            await fetch(`${API_BASE}/api/medical-images/${id}`, { method: 'DELETE' });
            loadImages();
            if (editImage?.id === id) setEditImage(null);
        } catch (e) {
            console.error('Error deleting:', e);
        }
    }, [loadImages, editImage]);

    // Open edit modal
    const handleOpenEdit = useCallback(async (img: MedicalImage) => {
        try {
            const res = await fetch(`${API_BASE}/api/medical-images/${img.id}/info`);
            const data = await res.json();
            if (data.success && data.image) {
                const fullImg = data.image;
                setEditImage({ ...img, ...fullImg });
                setEditTitle(fullImg.title || '');
                setEditKeywords((fullImg.keywords || []).join(', '));
                setEditCategory(fullImg.category || 'general');
                setEditDescription(fullImg.description || '');
            } else {
                setEditImage(img);
                setEditTitle(img.title || '');
                setEditKeywords((img.keywords || []).join(', '));
                setEditCategory(img.category || 'general');
                setEditDescription('');
            }
        } catch {
            setEditImage(img);
            setEditTitle(img.title || '');
            setEditKeywords((img.keywords || []).join(', '));
            setEditCategory(img.category || 'general');
            setEditDescription('');
        }
        setEditResult(null);
    }, []);

    // Save edit
    const handleSaveEdit = useCallback(async () => {
        if (!editImage) return;

        setEditSaving(true);
        setEditResult(null);

        try {
            const keywordsList = editKeywords.split(',').map(k => k.trim()).filter(Boolean);

            const res = await fetch(`${API_BASE}/api/medical-images/${editImage.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: editTitle,
                    description: editDescription,
                    category: editCategory,
                    keywords: keywordsList,
                }),
            });
            const data = await res.json();

            if (data.success) {
                const msgs: string[] = ['✅ Guardado'];
                if (data.keywords_changed) msgs.push('🔄 Keywords actualizados');
                if (data.embeddings_regenerated) msgs.push('🧬 Embeddings regenerados');
                if (data.message === 'No changes') msgs[0] = 'ℹ️ Sin cambios';

                setEditResult({ success: true, message: msgs.join(' • ') });
                loadImages();
            } else {
                setEditResult({ success: false, message: `❌ ${data.error || 'Error'}` });
            }
        } catch (e) {
            setEditResult({ success: false, message: `❌ ${e instanceof Error ? e.message : 'Error'}` });
        } finally {
            setEditSaving(false);
        }
    }, [editImage, editTitle, editKeywords, editCategory, editDescription, loadImages]);

    return (
        <div className="image-upload-panel">
            {/* Header */}
            <div className="iu-header">
                <div className="iu-header-title">
                    <Image size={16} />
                    <span>Imágenes Médicas</span>
                </div>
                <span className="iu-header-count">{images.length} registradas</span>
            </div>

            <div className="iu-content">
                {/* Upload Form */}
                <div className="iu-section">
                    <div className="iu-section-title">
                        <Upload size={14} />
                        <span>Subir Imagen</span>
                    </div>

                    {/* Drop Zone */}
                    <div
                        className={`iu-dropzone ${dragActive ? 'active' : ''} ${preview ? 'has-file' : ''}`}
                        onDragEnter={handleDrag}
                        onDragLeave={handleDrag}
                        onDragOver={handleDrag}
                        onDrop={handleDrop}
                        onClick={() => fileInputRef.current?.click()}
                    >
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="image/*"
                            style={{ display: 'none' }}
                            onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
                        />
                        {preview ? (
                            <div className="iu-preview-wrap">
                                <img src={preview} alt="Preview" className="iu-preview-img" />
                                <button
                                    className="iu-preview-remove"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        setFile(null);
                                        setPreview('');
                                    }}
                                >
                                    <X size={14} />
                                </button>
                            </div>
                        ) : (
                            <div className="iu-dropzone-content">
                                <Upload size={24} />
                                <span>Arrastra imagen o click para seleccionar</span>
                            </div>
                        )}
                    </div>

                    {/* Title */}
                    <div className="iu-field">
                        <label className="iu-label">
                            <FileText size={12} />
                            Título
                        </label>
                        <input
                            type="text"
                            className="iu-input"
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                            placeholder="Ej: Células en Canasta - LLC"
                        />
                    </div>

                    {/* Keywords */}
                    <div className="iu-field">
                        <label className="iu-label">
                            <Tag size={12} />
                            Keywords
                            <span className="iu-required">*</span>
                        </label>
                        <input
                            type="text"
                            className="iu-input"
                            value={keywords}
                            onChange={(e) => setKeywords(e.target.value)}
                            placeholder="leucemia, células en canasta, LLC"
                        />
                        <span className="iu-hint">Separados por coma. Se usarán para buscar la imagen.</span>
                    </div>

                    {/* Category */}
                    <div className="iu-field">
                        <label className="iu-label">
                            <FolderOpen size={12} />
                            Categoría
                        </label>
                        <select
                            className="iu-select"
                            value={category}
                            onChange={(e) => setCategory(e.target.value)}
                        >
                            {CATEGORIES.map((c) => (
                                <option key={c.value} value={c.value}>{c.label}</option>
                            ))}
                        </select>
                    </div>

                    {/* Description */}
                    <div className="iu-field">
                        <label className="iu-label">
                            <FileText size={12} />
                            Descripción
                        </label>
                        <textarea
                            className="iu-textarea"
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder="Descripción de la imagen..."
                            rows={2}
                        />
                    </div>

                    {/* Submit Button */}
                    <button
                        className="iu-submit"
                        onClick={handleSubmit}
                        disabled={!file || !keywords.trim() || uploading}
                    >
                        {uploading ? (
                            <>
                                <Loader2 size={14} className="iu-spin" />
                                Subiendo...
                            </>
                        ) : (
                            <>
                                <Upload size={14} />
                                Subir Imagen
                            </>
                        )}
                    </button>

                    {/* Result */}
                    {result && (
                        <div className={`iu-result ${result.success ? 'success' : 'error'}`}>
                            {result.success ? (
                                <>
                                    <div className="iu-result-header">
                                        <Check size={14} />
                                        <span>Imagen registrada (ID: {result.id})</span>
                                    </div>
                                    <div className="iu-result-details">
                                        <span className="iu-result-keywords">
                                            {result.keywords?.join(', ')}
                                        </span>
                                        <span className="iu-result-rag">
                                            RAG: {result.embeddings_generated ? '✅' : '⏳'}
                                        </span>
                                    </div>
                                    {result.http_url && (
                                        <div
                                            className="iu-result-url"
                                            onClick={() => {
                                                navigator.clipboard.writeText(result.http_url || '');
                                            }}
                                            title="Click para copiar URL"
                                        >
                                            <ExternalLink size={12} />
                                            {result.http_url}
                                        </div>
                                    )}
                                </>
                            ) : (
                                <div className="iu-result-header">
                                    <AlertCircle size={14} />
                                    <span>{result.error}</span>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Existing Images Grid */}
                <div className="iu-section">
                    <div className="iu-section-title">
                        <Image size={14} />
                        <span>Imágenes Registradas</span>
                        <span className="iu-badge">{filteredImages.length}{searchQuery && ` / ${images.length}`}</span>
                    </div>

                    {/* Search Bar */}
                    <div className="iu-search">
                        <Search size={14} className="iu-search-icon" />
                        <input
                            type="text"
                            className="iu-search-input"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="Buscar por título, keyword o categoría..."
                        />
                        {searchQuery && (
                            <button className="iu-search-clear" onClick={() => setSearchQuery('')}>
                                <X size={14} />
                            </button>
                        )}
                    </div>

                    {/* Image Grid */}
                    <div className="iu-grid">
                        {paginatedImages.length === 0 ? (
                            <div className="iu-empty">
                                {searchQuery ? `Sin resultados para "${searchQuery}"` : 'Sin imágenes registradas'}
                            </div>
                        ) : (
                            paginatedImages.map((img) => (
                                <div key={img.id} className="iu-card" onClick={() => handleOpenEdit(img)} style={{ cursor: 'pointer' }}>
                                    <div className="iu-card-img-wrap">
                                        <img
                                            src={`${API_BASE}/api/medical-images/${img.id}`}
                                            alt={img.title}
                                            className="iu-card-img"
                                            onError={(e) => {
                                                (e.target as HTMLImageElement).src =
                                                    'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><text x="20" y="55" font-size="40">🖼️</text></svg>';
                                            }}
                                        />
                                        <div className="iu-card-overlay">
                                            <Edit3 size={16} />
                                        </div>
                                        <button
                                            className="iu-card-delete"
                                            onClick={(e) => { e.stopPropagation(); handleDelete(img.id); }}
                                            title="Eliminar"
                                        >
                                            <Trash2 size={12} />
                                        </button>
                                    </div>
                                    <div className="iu-card-info">
                                        <div className="iu-card-title">{img.title || 'Sin título'}</div>
                                        <div className="iu-card-category">{img.category}</div>
                                        <div className="iu-card-keywords">
                                            {img.keywords?.slice(0, 3).map((kw) => (
                                                <span key={kw} className="iu-tag">{kw}</span>
                                            ))}
                                            {(img.keywords?.length || 0) > 3 && (
                                                <span className="iu-tag more">+{img.keywords.length - 3}</span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div className="iu-pagination">
                            <button
                                className="iu-page-btn"
                                disabled={currentPage <= 1}
                                onClick={() => setCurrentPage(p => p - 1)}
                            >
                                <ChevronLeft size={14} />
                            </button>
                            <div className="iu-page-numbers">
                                {Array.from({ length: totalPages }, (_, i) => i + 1).map(page => (
                                    <button
                                        key={page}
                                        className={`iu-page-num ${page === currentPage ? 'active' : ''}`}
                                        onClick={() => setCurrentPage(page)}
                                    >
                                        {page}
                                    </button>
                                ))}
                            </div>
                            <button
                                className="iu-page-btn"
                                disabled={currentPage >= totalPages}
                                onClick={() => setCurrentPage(p => p + 1)}
                            >
                                <ChevronRight size={14} />
                            </button>
                            <span className="iu-page-info">
                                {(currentPage - 1) * PAGE_SIZE + 1}-{Math.min(currentPage * PAGE_SIZE, filteredImages.length)} de {filteredImages.length}
                            </span>
                        </div>
                    )}
                </div>
            </div>

            {/* =================== EDIT MODAL =================== */}
            {editImage && (
                <div
                    className="iu-edit-overlay"
                    onClick={(e) => { if (e.target === e.currentTarget) setEditImage(null); }}
                >
                    <div className="iu-edit-modal">
                        {/* Modal Header */}
                        <div className="iu-edit-header">
                            <div className="iu-edit-header-title">
                                <Edit3 size={16} />
                                <span>Editar Imagen #{editImage.id}</span>
                            </div>
                            <button className="iu-edit-close" onClick={() => setEditImage(null)}>
                                <X size={18} />
                            </button>
                        </div>

                        <div className="iu-edit-body">
                            {/* Image Preview */}
                            <div className="iu-edit-preview">
                                <img
                                    src={`${API_BASE}/api/medical-images/${editImage.id}`}
                                    alt={editTitle}
                                    className="iu-edit-img"
                                    onError={(e) => {
                                        (e.target as HTMLImageElement).src =
                                            'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200"><text x="60" y="120" font-size="80">🖼️</text></svg>';
                                    }}
                                />
                            </div>

                            {/* Edit Fields */}
                            <div className="iu-edit-fields">
                                <div className="iu-field">
                                    <label className="iu-label">
                                        <FileText size={12} />
                                        Título
                                    </label>
                                    <input
                                        type="text"
                                        className="iu-input"
                                        value={editTitle}
                                        onChange={(e) => setEditTitle(e.target.value)}
                                        placeholder="Título de la imagen"
                                    />
                                </div>

                                <div className="iu-field">
                                    <label className="iu-label">
                                        <Tag size={12} />
                                        Keywords
                                        <span className="iu-hint-inline">
                                            (cambiar regenera embeddings)
                                        </span>
                                    </label>
                                    <input
                                        type="text"
                                        className="iu-input"
                                        value={editKeywords}
                                        onChange={(e) => setEditKeywords(e.target.value)}
                                        placeholder="keyword1, keyword2, keyword3"
                                    />
                                </div>

                                <div className="iu-field">
                                    <label className="iu-label">
                                        <FolderOpen size={12} />
                                        Categoría
                                    </label>
                                    <select
                                        className="iu-select"
                                        value={editCategory}
                                        onChange={(e) => setEditCategory(e.target.value)}
                                    >
                                        {CATEGORIES.map((c) => (
                                            <option key={c.value} value={c.value}>{c.label}</option>
                                        ))}
                                    </select>
                                </div>

                                <div className="iu-field">
                                    <label className="iu-label">
                                        <FileText size={12} />
                                        Descripción
                                    </label>
                                    <textarea
                                        className="iu-textarea"
                                        value={editDescription}
                                        onChange={(e) => setEditDescription(e.target.value)}
                                        placeholder="Descripción de la imagen..."
                                        rows={3}
                                    />
                                </div>
                            </div>
                        </div>

                        {/* Modal Footer */}
                        <div className="iu-edit-footer">
                            {editResult && (
                                <div className={`iu-edit-result ${editResult.success ? 'success' : 'error'}`}>
                                    {editResult.message}
                                </div>
                            )}
                            <div className="iu-edit-actions">
                                <button
                                    className="iu-edit-btn-delete"
                                    onClick={() => handleDelete(editImage.id)}
                                >
                                    <Trash2 size={14} />
                                    Eliminar
                                </button>
                                <div style={{ flex: 1 }} />
                                <button
                                    className="iu-edit-btn-cancel"
                                    onClick={() => setEditImage(null)}
                                >
                                    Cancelar
                                </button>
                                <button
                                    className="iu-edit-btn-save"
                                    onClick={handleSaveEdit}
                                    disabled={editSaving}
                                >
                                    {editSaving ? (
                                        <>
                                            <Loader2 size={14} className="iu-spin" />
                                            Guardando...
                                        </>
                                    ) : (
                                        <>
                                            <Save size={14} />
                                            Guardar
                                        </>
                                    )}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
