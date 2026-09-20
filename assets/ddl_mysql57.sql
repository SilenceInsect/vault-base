CREATE DATABASE IF NOT EXISTS `amrd_qa_vault`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `amrd_qa_vault`;

-- ============================================================
-- 1. 共享金库主表：已审核通过的公共知识，对外查询核心表
-- ============================================================
CREATE TABLE IF NOT EXISTS `shared_vault_main` (
  `id`             BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `uuid`           CHAR(36)         NOT NULL COMMENT '来源 MD 的 uuid',
  `schema_version` TINYINT UNSIGNED NOT NULL DEFAULT 1,
  `skill_id`       VARCHAR(64)      NOT NULL,
  `doc_type`       ENUM('answer','decision','reference','brief') NOT NULL,
  `title`          VARCHAR(255)     NOT NULL,
  `body`           MEDIUMTEXT       NOT NULL COMMENT '完整正文（含 section 标题）',
  `segments`       JSON             NULL COMMENT '结构化分段：question/answer/basis/remark',
  `tags`           JSON             NOT NULL COMMENT '标签数组，首项为领域标签',
  `author`         VARCHAR(64)      NOT NULL,
  `author_ip`      VARCHAR(45)      NOT NULL,
  `source_task_id` VARCHAR(64)      NULL,
  `source_ref`     VARCHAR(512)     NULL,
  `content_hash`   CHAR(16)         NOT NULL COMMENT '正文规范化 sha256 前 16 位',
  `share_rev`      INT UNSIGNED     NOT NULL DEFAULT 1,
  `doc_rev`        INT UNSIGNED     NOT NULL DEFAULT 1 COMMENT '文档自身修订号：brief 取 brief_rev，其他类型恒为 1',
  `claim_key`      VARCHAR(191)     NULL COMMENT '断言锚点 {领域}:{对象}:{属性}，可空；空则不参与精确矛盾检测',
  `status`         ENUM('active','superseded','archived') NOT NULL DEFAULT 'active',
  `supersedes`     CHAR(36)         NULL COMMENT '取代了哪条（指向旧条目 uuid）',
  `superseded_by`  CHAR(36)         NULL COMMENT '被哪条取代（指向新条目 uuid）',
  `dup_of`         CHAR(36)         NULL COMMENT '与哪条已有知识高度相似（仅提示，不入库阻断）',
  `dup_score`      DECIMAL(6,3)     NULL COMMENT 'ngram 全文索引相关度分数，非归一化，需标定',
  `reviewer`       VARCHAR(64)      NOT NULL,
  `reviewed_at`    DATETIME(3)      NOT NULL,
  `review_note`    VARCHAR(512)     NULL,
  `created_at`     DATETIME(3)      NOT NULL COMMENT '来源 MD 的 created_at',
  `updated_at`     DATETIME(3)      NOT NULL COMMENT '来源 MD 的 updated_at',
  `synced_at`      DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_uuid` (`uuid`),
  KEY `idx_skill_type` (`skill_id`, `doc_type`, `status`),
  KEY `idx_claim` (`claim_key`, `status`),
  KEY `idx_supersede` (`superseded_by`),
  KEY `idx_dup` (`dup_of`),
  KEY `idx_author` (`author`),
  KEY `idx_updated` (`updated_at`),
  KEY `idx_hash` (`content_hash`),
  FULLTEXT KEY `ft_search` (`title`, `body`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='共享金库主表';

-- ============================================================
-- 2. 待审核队列表
-- ============================================================
CREATE TABLE IF NOT EXISTS `shared_vault_pending_review` (
  `id`             BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `uuid`           CHAR(36)         NOT NULL,
  `schema_version` TINYINT UNSIGNED NOT NULL DEFAULT 1,
  `skill_id`       VARCHAR(64)      NOT NULL,
  `doc_type`       ENUM('answer','decision','reference','brief') NOT NULL,
  `title`          VARCHAR(255)     NOT NULL,
  `body`           MEDIUMTEXT       NOT NULL,
  `segments`       JSON             NULL,
  `tags`           JSON             NOT NULL,
  `author`         VARCHAR(64)      NOT NULL,
  `author_ip`      VARCHAR(45)      NOT NULL,
  `source_task_id` VARCHAR(64)      NULL,
  `source_ref`     VARCHAR(512)     NULL,
  `content_hash`   CHAR(16)         NOT NULL,
  `share_rev`      INT UNSIGNED     NOT NULL DEFAULT 1,
  `doc_rev`        INT UNSIGNED     NOT NULL DEFAULT 1 COMMENT '文档自身修订号：brief 取 brief_rev，其他类型恒为 1',
  `claim_key`      VARCHAR(191)     NULL COMMENT '断言锚点，可空',
  `dup_candidates` JSON             NULL COMMENT '相似候选 [{uuid,title,score}]，仅提示',
  `conflict_candidates` JSON        NULL COMMENT '矛盾候选 [{uuid,title,claim_key,body_sim}]',
  `needs_llm`      TINYINT(1)       NOT NULL DEFAULT 0 COMMENT '0=已判定 1=待 LLM 判定（仅会话内可处理）',
  `defer_reason`   VARCHAR(255)     NULL COMMENT 'needs_llm=1 的原因，便于 P1 优先处理',
  `status`         ENUM('pending','approved','rejected','superseded')
                     NOT NULL DEFAULT 'pending',
  `llm_verdict`    ENUM('share','private','uncertain') NULL COMMENT 'LLM 判定结果',
  `llm_reason`     VARCHAR(512)     NULL,
  `llm_confidence` DECIMAL(4,3)     NULL,
  `enqueued_at`    DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `reviewer`       VARCHAR(64)      NULL,
  `reviewed_at`    DATETIME(3)      NULL,
  `review_note`    VARCHAR(512)     NULL,
  `source_event_id` CHAR(32)        NULL COMMENT '来源事件的幂等键',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_uuid_rev` (`uuid`, `share_rev`),
  KEY `idx_status_time` (`status`, `enqueued_at`),
  KEY `idx_claim` (`claim_key`),
  KEY `idx_needs_llm` (`needs_llm`, `status`),
  KEY `idx_author` (`author`),
  FULLTEXT KEY `ft_search` (`title`, `body`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='共享知识待审核队列';

-- ============================================================
-- 3. 全量审计日志表
-- ============================================================
CREATE TABLE IF NOT EXISTS `shared_vault_audit_log` (
  `id`           BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `uuid`         CHAR(36)         NOT NULL,
  `action`       ENUM('submit','approve','reject','update','supersede',
                      'restore','archive','migrate','merge','defer') NOT NULL,
  `actor`        VARCHAR(64)      NOT NULL,
  `actor_ip`     VARCHAR(45)      NULL,
  `target_table` VARCHAR(64)      NULL,
  `before_hash`  CHAR(16)         NULL,
  `after_hash`   CHAR(16)         NULL,
  `snapshot`     JSON             NULL COMMENT '变更后完整记录，DELETE 类为变更前',
  `reason`       VARCHAR(512)     NULL,
  `created_at`   DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_uuid_time` (`uuid`, `created_at`),
  KEY `idx_action_time` (`action`, `created_at`),
  KEY `idx_actor_time` (`actor`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='共享知识全生命周期审计';

-- ============================================================
-- 4. 事件表（可选后端，event_backend=mysql 时启用）
-- ============================================================
CREATE TABLE IF NOT EXISTS `vault_event` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `event_id`    CHAR(32)        NOT NULL COMMENT '幂等键',
  `user_id`     VARCHAR(64)     NOT NULL,
  `skill_id`    VARCHAR(64)     NOT NULL,
  `doc_uuid`    CHAR(36)        NOT NULL,
  `change_type` ENUM('A','M','D','R') NOT NULL,
  `old_path`    VARCHAR(512)    NULL COMMENT 'R 类型的原路径',
  `file_path`   VARCHAR(512)    NOT NULL,
  `blob_hash`   CHAR(40)        NULL COMMENT 'D 类型为 NULL',
  `base_commit` CHAR(40)        NULL,
  `head_commit` CHAR(40)        NULL,
  `payload`     JSON            NOT NULL,
  `needs_llm`   TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '1=规则判不了，仅会话内可处理',
  `status`      ENUM('pending','processing','done','dead') NOT NULL DEFAULT 'pending',
  `owner`       VARCHAR(64)     NULL,
  `lease_until` DATETIME(3)     NULL,
  `attempts`    INT UNSIGNED    NOT NULL DEFAULT 0,
  `last_error`  VARCHAR(512)    NULL,
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `finished_at` DATETIME(3)     NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_event` (`event_id`),
  KEY `idx_status_id` (`status`, `id`),
  KEY `idx_lease` (`status`, `lease_until`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='事件队列（MySQL 后端）';
