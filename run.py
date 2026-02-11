#!/usr/bin/env python
"""Flask Application Entry Point"""

import os
from app import create_app

# Create Flask application
app = create_app(os.environ.get('FLASK_ENV', 'development'))

if __name__ == '__main__':
    # Get port from environment or use default
    port = int(os.environ.get('PORT', 5001))
    host = os.environ.get('HOST', '0.0.0.0')
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'

    print(f"""
    ========================================
    SUUMO Property Scraper - Web Edition
    ========================================

    Server running at: http://{host}:{port}

    Press Ctrl+C to stop.
    ========================================
    """)

    app.run(host=host, port=port, debug=debug, threaded=True)
