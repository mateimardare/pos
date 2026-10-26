"""Client gRPC simplu pentru testarea serviciului IDM.

Exemple:
  python client.py login admin 'parola-admin-lunga'
  python client.py --token <JWT> list
  python client.py --token <JWT> create ion user
  python client.py --token <JWT> deactivate <uid>
  python client.py --token <JWT> activate <uid>
"""
import argparse
import os

import grpc

import idm_pb2
import idm_pb2_grpc

def main():
    parser = argparse.ArgumentParser(description="Client IDM gRPC")
    parser.add_argument("--target", default=os.getenv("IDM_TARGET", "localhost:50051"))
    parser.add_argument("--token", default=os.getenv("IDM_TOKEN", ""))
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("login"); p.add_argument("username"); p.add_argument("password")
    sub.add_parser("list")
    p = sub.add_parser("create"); p.add_argument("username"); p.add_argument("role")
    p = sub.add_parser("deactivate"); p.add_argument("uid")
    p = sub.add_parser("activate"); p.add_argument("uid")
    args = parser.parse_args()

    metadata = [("authorization", f"Bearer {args.token}")] if args.token else []
    with grpc.insecure_channel(args.target) as channel:
        stub = idm_pb2_grpc.IdentityServiceStub(channel)
        try:
            if args.cmd == "login":
                resp = stub.Login(idm_pb2.LoginRequest(username=args.username, password=args.password))
            elif args.cmd == "list":
                resp = stub.ListUsers(idm_pb2.ListUsersRequest(), metadata=metadata)
            elif args.cmd == "create":
                resp = stub.CreateUser(
                    idm_pb2.CreateUserRequest(username=args.username, role=args.role), metadata=metadata)
            elif args.cmd == "deactivate":
                resp = stub.DeactivateUser(idm_pb2.UserIdRequest(uid=args.uid), metadata=metadata)
            else:
                resp = stub.ActivateUser(idm_pb2.UserIdRequest(uid=args.uid), metadata=metadata)
            print(resp)
        except grpc.RpcError as e:
            raise SystemExit(f"{e.code().name}: {e.details()}")

if __name__ == "__main__":
    main()
