// Runs once, on the first start of the mongo container (Lab 3 - "Securing a MongoDB server").
// MONGO_INITDB_ROOT_* creates the bootstrap root user; here we add the users the lab asks for.
const env = process.env;
const admin = db.getSiblingDB("admin");

// 1. user administrator of the instance
admin.createUser({
  user: env.MONGO_USERADMIN_USER,
  pwd: env.MONGO_USERADMIN_PASSWORD,
  roles: [{ role: "userAdminAnyDatabase", db: "admin" }],
});

// 2. for the application database: an owner and a readWrite user (the service uses the latter)
const appDb = db.getSiblingDB(env.MONGO_APP_DB);
appDb.createUser({
  user: env.MONGO_OWNER_USER,
  pwd: env.MONGO_OWNER_PASSWORD,
  roles: [{ role: "dbOwner", db: env.MONGO_APP_DB }],
});
appDb.createUser({
  user: env.MONGO_APP_USER,
  pwd: env.MONGO_APP_PASSWORD,
  roles: [{ role: "readWrite", db: env.MONGO_APP_DB }],
});
