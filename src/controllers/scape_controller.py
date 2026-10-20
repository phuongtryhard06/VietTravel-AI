from flask import request
from flask_restx import Namespace, Resource
from werkzeug.utils import secure_filename
import os
import traceback
from src.services.scape_service import import_csv_to_attractions

# Tạo namespace cho scape
scape_ns = Namespace('scape', description='Scape data operations')

@scape_ns.route('/import-diadiem-scape')
class ImportDiadiemScape(Resource):
    @scape_ns.doc('import_diadiem_scape')
    def post(self):
        """
        Import dữ liệu từ file diadiem_scape.csv trong thư mục src/scape
        
        Returns:
        - JSON response với kết quả import
        """
        try:
            # Đường dẫn đến file diadiem_scape.csv
            csv_file_path = 'src/scape/diadiem_scape.csv'
            
            # Kiểm tra file có tồn tại không
            if not os.path.exists(csv_file_path):
                return {
                    'success': False,
                    'error': f'❌ File không tồn tại: {csv_file_path}',
                    'details': {
                        'file_path': csv_file_path,
                        'current_directory': os.getcwd(),
                        'available_files': os.listdir('src/scape') if os.path.exists('src/scape') else [],
                        'suggestion': 'Hãy kiểm tra đường dẫn file và đảm bảo file diadiem_scape.csv tồn tại trong thư mục src/scape'
                    }
                }, 404
            
            # Kiểm tra file có đọc được không
            try:
                with open(csv_file_path, 'r', encoding='utf-8') as f:
                    first_line = f.readline().strip()
            except UnicodeDecodeError as e:
                return {
                    'success': False,
                    'error': f'❌ Lỗi encoding file: {str(e)}',
                    'details': {
                        'file_path': csv_file_path,
                        'suggestion': 'Hãy đảm bảo file CSV được lưu với encoding UTF-8',
                        'solution': 'Mở file trong Notepad++ hoặc VS Code và lưu lại với encoding UTF-8'
                    }
                }, 400
            except Exception as e:
                return {
                    'success': False,
                    'error': f'❌ Không thể đọc file: {str(e)}',
                    'details': {
                        'file_path': csv_file_path,
                        'file_size': os.path.getsize(csv_file_path) if os.path.exists(csv_file_path) else 0,
                        'suggestion': 'Kiểm tra quyền truy cập file và định dạng file'
                    }
                }, 400
            
            # Gọi service để import dữ liệu
            result = import_csv_to_attractions(csv_file_path)
            
            if result['success']:
                success_msg = f"✅ Import thành công {result['imported_count']} records từ {result['total_rows']} dòng"
                
                return {
                    'success': True,
                    'message': success_msg,
                    'data': {
                        'imported_count': result['imported_count'],
                        'total_rows': result['total_rows'],
                        'error_count': len(result['errors']),
                        'errors': result['errors'][:20] if result['errors'] else [],  # Chỉ trả về 20 lỗi đầu
                        'success_rate': f"{(result['imported_count'] / result['total_rows'] * 100):.1f}%" if result['total_rows'] > 0 else "0%",
                        'summary': {
                            'total_processed': result['total_rows'],
                            'successful_imports': result['imported_count'],
                            'failed_imports': len(result['errors']),
                            'success_percentage': f"{(result['imported_count'] / result['total_rows'] * 100):.1f}%"
                        }
                    }
                }, 200
            else:
                return {
                    'success': False,
                    'error': f"❌ Lỗi import: {result['error']}",
                    'details': result.get('details', {}),
                    'file_path': csv_file_path,
                    'imported_count': result.get('imported_count', 0),
                    'total_rows': result.get('total_rows', 0)
                }, 500
                
        except Exception as e:
            # Lấy stack trace chi tiết
            error_trace = traceback.format_exc()
            error_msg = f"❌ Lỗi không mong muốn: {str(e)}"
            
            return {
                'success': False,
                'error': error_msg,
                'details': {
                    'exception_type': type(e).__name__,
                    'file_path': csv_file_path if 'csv_file_path' in locals() else 'Unknown',
                    'stack_trace': error_trace.split('\n')[-5:] if len(error_trace.split('\n')) > 5 else error_trace.split('\n'),
                    'suggestion': 'Kiểm tra lại file CSV và thử lại. Nếu lỗi vẫn tiếp tục, hãy liên hệ admin.'
                }
            }, 500
