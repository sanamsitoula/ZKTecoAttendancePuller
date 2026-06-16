"""
Device Manager CLI — called from PHP via exec()
Usage:
  python device_manager.py list_users   --device_ip 10.10.10.18 [--port 4370]
  python device_manager.py delete_user  --device_ip 10.10.10.18 --uid 32 --user_id 342
  python device_manager.py set_user     --device_ip 10.10.10.18 --uid 32 --user_id 342 --name "John Doe"
Returns JSON to stdout.
"""
import sys, json, argparse
try:
    from zk import ZK
    from zk.exception import ZKConnectionUnsuccessful
except ImportError:
    print(json.dumps({'success': False, 'error': 'pyzk not installed'}))
    sys.exit(1)

def connect(ip, port, password=0, timeout=10):
    zk = ZK(ip, port=int(port), timeout=int(timeout),
            password=int(password), ommit_ping=True)
    return zk, zk.connect()

def list_users(args):
    zk, conn = connect(args.device_ip, args.port)
    try:
        conn.disable_device()
        users = conn.get_users()
        result = [{'uid': u.uid, 'user_id': u.user_id, 'name': u.name,
                   'privilege': u.privilege, 'card': u.card} for u in users]
        conn.enable_device()
        conn.disconnect()
        print(json.dumps({'success': True, 'count': len(result), 'users': result}))
    except Exception as e:
        try: conn.enable_device(); conn.disconnect()
        except: pass
        print(json.dumps({'success': False, 'error': str(e)}))

def delete_user(args):
    zk, conn = connect(args.device_ip, args.port)
    try:
        conn.disable_device()
        conn.delete_user(uid=int(args.uid), user_id=str(args.user_id))
        conn.enable_device()
        conn.disconnect()
        print(json.dumps({'success': True,
                          'message': f'User uid={args.uid} user_id={args.user_id} deleted from {args.device_ip}'}))
    except Exception as e:
        try: conn.enable_device(); conn.disconnect()
        except: pass
        print(json.dumps({'success': False, 'error': str(e)}))

def set_user(args):
    zk, conn = connect(args.device_ip, args.port)
    try:
        conn.disable_device()
        conn.set_user(uid=int(args.uid), user_id=str(args.user_id),
                      name=str(args.name or '')[:24],
                      privilege=int(args.privilege or 0))
        conn.enable_device()
        conn.disconnect()
        print(json.dumps({'success': True,
                          'message': f'User uid={args.uid} set on {args.device_ip}'}))
    except Exception as e:
        try: conn.enable_device(); conn.disconnect()
        except: pass
        print(json.dumps({'success': False, 'error': str(e)}))

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['list_users','delete_user','set_user'])
    p.add_argument('--device_ip', required=True)
    p.add_argument('--port',      default=4370)
    p.add_argument('--password',  default=0)
    p.add_argument('--timeout',   default=10)
    p.add_argument('--uid',       default=0)
    p.add_argument('--user_id',   default='')
    p.add_argument('--name',      default='')
    p.add_argument('--privilege', default=0)
    args = p.parse_args()

    try:
        if   args.command == 'list_users':  list_users(args)
        elif args.command == 'delete_user': delete_user(args)
        elif args.command == 'set_user':    set_user(args)
    except ZKConnectionUnsuccessful:
        print(json.dumps({'success': False, 'error': f'Cannot connect to device {args.device_ip}:{args.port}'}))
    except Exception as e:
        print(json.dumps({'success': False, 'error': str(e)}))
