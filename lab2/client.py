"""Aplicație de consolă pentru serviciul IDM.

Exemple:
  python client.py login admin 'parola-admin-lunga'
  python client.py --token <JWT> validate
  python client.py --token <JWT> list
  python client.py --token <JWT> create ion maintainer
  python client.py activate ion 'parola-lui-ion-lunga'
  python client.py --token <JWT> deactivate ion
  python client.py --token <JWT> logout
"""
import argparse
import os

import grpc

from idm_client import IdmClient

def main():
    parser = argparse.ArgumentParser(description="Client IDM gRPC")
    parser.add_argument("--target", default=os.getenv("IDM_TARGET", "localhost:50051"))
    parser.add_argument("--token", default=os.getenv("IDM_TOKEN", ""))
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("login"); p.add_argument("username"); p.add_argument("password")
    sub.add_parser("validate")
    sub.add_parser("logout")
    sub.add_parser("list")
    p = sub.add_parser("create"); p.add_argument("username"); p.add_argument("role")
    p = sub.add_parser("activate"); p.add_argument("username"); p.add_argument("password")
    p = sub.add_parser("deactivate"); p.add_argument("username")
    args = parser.parse_args()

    with IdmClient(args.target) as idm:
        try:
            if args.cmd == "login":
                print(idm.login(args.username, args.password))
            elif args.cmd == "validate":
                print(idm.validate(args.token))
            elif args.cmd == "logout":
                print(idm.destroy(args.token))
            elif args.cmd == "list":
                for u in idm.list_users(args.token):
                    print(f"{u.uid}  {u.username:<20} {u.role:<14} {'activ' if u.is_active else 'inactiv'}")
            elif args.cmd == "create":
                print(idm.create_user(args.token, args.username, args.role))
            elif args.cmd == "activate":
                print(idm.activate(args.username, args.password))
            else:
                print(idm.deactivate(args.token, args.username))
        except grpc.RpcError as e:
            raise SystemExit(f"{e.code().name}: {e.details()}")

if __name__ == "__main__":
    main()
