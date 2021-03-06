import json
import logging
from flask import jsonify, Flask, redirect, request, send_file
from flask_cors import CORS
from os.path import exists
from rq import Queue
from qmk_compiler import STORAGE_ENGINE, FILESYSTEM_PATH, MINIO_BUCKET, compile_firmware, minio, redis

if exists('version.txt'):
    __VERSION__ = open('version.txt').read()
else:
    __VERSION__ = '__UNKNOWN__'

# Useful objects
app = Flask(__name__)
cors = CORS(app, resources={'/v*/*': {'origins': '*'}})
rq = Queue(connection=redis)

## Helper functions
def error(message, code=400, **kwargs):
    """Return a structured JSON error message.
    """
    kwargs['message'] = message
    return jsonify(kwargs), code


def get_job_metadata(job_id):
    """Fetch a job's metadata from the file store.
    """
    if STORAGE_ENGINE == 'minio':
        json_text = minio.get_object(MINIO_BUCKET, '%s/%s.json' % (job_id, job_id))
        return json.loads(json_text.data.decode('utf-8'))
    else:
        json_path = '%s/%s/%s.json' % (FILESYSTEM_PATH, job_id, job_id)
        if exists(json_path):
            return json.load(open(json_path))


## Views
@app.route('/', methods=['GET'])
def root():
    """Serve up the documentation for this API.
    """
    return redirect('https://docs.compile.qmk.fm/')


@app.route('/v1', methods=['GET'])
def GET_v1():
    """Return the API's status.
    """
    return jsonify({
        'status': 'running',
        'version': __VERSION__
    })


@app.route('/v1/compile', methods=['POST'])
def POST_v1_compile():
    """Enqueue a compile job.
    """
    data = request.get_json(force=True)
    if not data:
        return error("Invalid JSON data!")

    if '.' in data['keyboard'] or '/' in data['keymap']:
        return error("Fuck off hacker.", 422)

    job = compile_firmware.delay(data['keyboard'], data['keymap'], data['layout'], data['layers'])
    return jsonify({'enqueued': True, 'job_id': job.id})


@app.route('/v1/compile/<string:job_id>', methods=['GET'])
def GET_v1_compile_job_id(job_id):
    """Fetch the status of a compile job.
    """
    # Check redis first.
    job = rq.fetch_job(job_id)
    if job:
        if job.is_finished:
            status = 'finished'
        elif job.is_queued:
            status = 'queued'
        elif job.is_started:
            status = 'running'
        elif job.is_failed:
            status = 'failed'
        else:
            logging.error('Unknown job status!')
            status = 'unknown'
        return jsonify({
            'created_at': job.created_at,
            'enqueued_at': job.enqueued_at,
            'id': job.id,
            'is_failed': job.is_failed,
            'status': status,
            'result': job.result
        })

    # Check for cached json if it's not in redis
    job = get_job_metadata(job_id)
    if job:
        return jsonify(job)

    # Couldn't find it
    return error("Compile job not found", 404)


@app.route('/v1/compile/<string:job_id>/hex', methods=['GET'])
def GET_v1_compile_job_id_hex(job_id):
    """Download a compiled firmware
    """
    job = get_job_metadata(job_id)
    if not job:
        return error("Compile job not found", 404)

    if job['result']['firmware']:
        if STORAGE_ENGINE == 'minio':
            firmware_file = minio.get_object(MINIO_BUCKET, '/'.join((job_id, job['result']['firmware_filename'])))
            return send_file(firmware_file, mimetype='application/octet-stream', as_attachment=True, attachment_filename=job['result']['firmware_filename'])
        else:
            filename = '/'.join((FILESYSTEM_PATH, job_id, job['result']['firmware_filename']))
            if exists(filename):
                return send_file(filename, mimetype='application/octet-stream', as_attachment=True, attachment_filename=job['result']['firmware_filename'])

    return error("Compile job not finished or other error.", 422)


@app.route('/v1/compile/<string:job_id>/source', methods=['GET'])
def GET_v1_compile_job_id_src(job_id):
    """Download a completed compile job.
    """
    job = get_job_metadata(job_id)
    if not job:
        return error("Compile job not found", 404)

    if job['result']['firmware']:
        if STORAGE_ENGINE == 'minio':
            firmware_file = minio.get_object(MINIO_BUCKET, '/'.join((job_id, job['result']['source_archive'])))
            return send_file(firmware_file, mimetype='application/octet-stream', as_attachment=True, attachment_filename=job['result']['source_archive'])
        else:
            filename = '/'.join((FILESYSTEM_PATH, job_id, job['result']['firmware_filename']))
            if exists(filename):
                return send_file(filename, 'application/octet-stream', as_attachment=True, attachment_filename=filename)

    return error("Compile job not finished or other error.", 422)


if __name__ == '__main__':
    # Start the webserver
    app.run(debug=True)
