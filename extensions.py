from flask_cors import CORS

cors = CORS(resources={r"/*": {"origins": "*"}}, supports_credentials=True)