CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB);
CREATE TABLE composerHeaders (
  composerId TEXT PRIMARY KEY,
  workspaceId TEXT,
  lastUpdatedAt INTEGER,
  recency INTEGER,
  isArchived INTEGER,
  isSubagent INTEGER,
  value BLOB
);
CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB);

INSERT INTO ItemTable (key, value) VALUES
  ('cursor/glass.selectedAgent', 'composer-1'),
  (
    'glass.localAgentProjectMembership.v1',
    '{"composer-1": {}, "composer-2": {}, "composer-draft": {}, "composer-archived": {}}'
  );

INSERT INTO composerHeaders (
  composerId,
  workspaceId,
  lastUpdatedAt,
  recency,
  isArchived,
  isSubagent,
  value
) VALUES
  (
    'composer-1',
    'workspace-1',
    100,
    200,
    0,
    0,
    '{"name": "Foundation work", "agentLocation": {"environment": {"uri": {"fsPath": "/tmp/elchango"}}}}'
  ),
  (
    'composer-2',
    'workspace-2',
    50,
    100,
    0,
    0,
    '{}'
  ),
  (
    'composer-draft',
    'workspace-3',
    40,
    90,
    0,
    0,
    '{"isDraft": true}'
  ),
  (
    'composer-archived',
    'workspace-4',
    30,
    80,
    1,
    0,
    '{}'
  ),
  (
    'composer-nonmember',
    'workspace-5',
    20,
    70,
    0,
    0,
    '{}'
  );

INSERT INTO cursorDiskKV (key, value) VALUES
  (
    'composerData:composer-1',
    '{"fullConversationHeadersOnly": [{"bubbleId": "bubble-1"}]}'
  ),
  (
    'bubbleId:composer-1:bubble-1',
    '{"toolFormerData": {"status": "loading"}}'
  );
