import pandas as pd
import chromadb
from chromadb.utils import embedding_functions
import os
import json

def process_diadiem():
    # Đường dẫn đến file diadiem.csv
    workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    csv_path = os.path.join(workspace_root, 'src', 'scape', 'diadiem.csv')
    
    # Kiểm tra file tồn tại
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Không tìm thấy file diadiem.csv tại đường dẫn: {csv_path}")

    # Đọc file CSV
    df = pd.read_csv(csv_path, encoding='utf-8')
    
    # Chuẩn hóa dữ liệu
    df['ten_dia_diem'] = df['ten_dia_diem'].str.strip()
    df['mo_ta'] = df['mo_ta'].str.strip()
    
    # Tạo document text phong phú hơn
    df['document'] = df.apply(lambda row: f"""
        Địa điểm: {row['ten_dia_diem']}
        Loại địa điểm: {row.get('loai_dia_diem', '')}
        Khu vực: {row.get('khu_vuc', '')}
        Địa chỉ: {row.get('dia_chi', '')}
        Mô tả: {row['mo_ta']}
        Từ khóa: {row.get('tu_khoa', '')}
        Thời gian hoạt động: {row.get('thoi_gian_hoat_dong', '')}
        Giá vé: {row.get('gia_ve', '')}
        Đánh giá: {row.get('danh_gia', '')}
    """.strip(), axis=1)
    
    # Khởi tạo ChromaDB client
    chroma_client = chromadb.PersistentClient(path=os.path.join(workspace_root, 'src', 'nlp_model', 'data', 'chroma_db'))
    
    # Sử dụng sentence-transformers làm embedding function
    sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2"
    )
    
    # Xóa collection cũ nếu tồn tại
    try:
        chroma_client.delete_collection("diadiem_collection")
        print("Đã xóa collection cũ")
    except:
        print("Collection chưa tồn tại, tạo mới")
    
    # Tạo collection mới
    collection = chroma_client.create_collection(
        name="diadiem_collection",
        embedding_function=sentence_transformer_ef
    )
    
    # Thêm dữ liệu mới
    collection.add(
        ids=df['id'].astype(str).tolist(),
        documents=df['document'].tolist(),
        metadatas=df.to_dict('records')
    )
    
    print(f"Đã xử lý và lưu {len(df)} địa điểm vào ChromaDB")

