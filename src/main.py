import sys
import logging
from PyQt5.QtWidgets import QApplication
from src.views.main_window import MainWindow

# Настройка логирования
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='app_log.txt',
    filemode='w'
)

logger = logging.getLogger(__name__)

def main():
    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        logger.info("Application started")
        sys.exit(app.exec_())
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)

if __name__ == '__main__':
    main()