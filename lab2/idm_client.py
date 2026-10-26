"""Bibliotecă client pentru serviciul IDM gRPC."""
import grpc

import idm_pb2
import idm_pb2_grpc

class IdmClient:
    def __init__(self, target="localhost:50051"):
        self._ch = grpc.insecure_channel(target)
        self._stub = idm_pb2_grpc.IdentityServiceStub(self._ch)

    def login(self, username, password):
        return self._stub.Login(idm_pb2.LoginRequest(username=username, password=password)).token

    def list_users(self, token):
        return list(self._stub.ListUsers(idm_pb2.TokenMessage(token=token)).users)

    def create_user(self, token, username, role):
        return self._stub.CreateUser(idm_pb2.CreateUserRequest(token=token, username=username, role=role))

    def deactivate(self, token, username):
        return self._stub.DeactivateUser(idm_pb2.DeactivateUserRequest(token=token, username=username))

    def activate(self, username, password):
        return self._stub.ActivateUser(idm_pb2.ActivateUserRequest(username=username, password=password))

    def validate(self, token):
        return self._stub.ValidateToken(idm_pb2.TokenMessage(token=token))

    def destroy(self, token):
        return self._stub.DestroyToken(idm_pb2.TokenMessage(token=token))

    def close(self):
        self._ch.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
