<template>
  <div class="chapter-status-panel">
    <n-empty v-if="!chapter" description="请从左侧选择一个章节" style="margin-top: 48px" />

    <n-space v-else vertical :size="12" style="width: 100%; padding: 8px 4px">
      <!-- 章节基本信息 -->
      <n-card size="small" :bordered="true" class="status-card">
        <div class="chapter-header">
          <div class="chapter-title-row">
            <n-text class="chapter-number">第 {{ chapter.number }} 章</n-text>
            <n-text class="chapter-title">{{ chapter.title || '未命名' }}</n-text>
          </div>
          <div class="chapter-meta">
            <n-tag :type="chapter.word_count > 0 ? 'success' : 'default'" size="small" round>
              {{ chapter.word_count > 0 ? '已收稿' : '未收稿' }}
            </n-tag>
            <n-text depth="3" class="word-count">{{ chapter.word_count ?? 0 }} 字</n-text>
          </div>
        </div>
      </n-card>

      <n-alert v-if="readOnly" type="warning" :show-icon="true" size="small">
        全托管执行中，辅助撰稿区仅可阅读
      </n-alert>

      <!-- 人工审阅 -->
      <n-spin :show="metaLoading">
        <n-card v-if="slug" size="small" :bordered="true" class="status-card">
          <template #header>
            <span class="card-title">📝 人工审阅</span>
          </template>
          <n-empty v-if="!chapterReview && !metaLoading" description="暂无审阅记录" size="small" />
          <div v-else-if="chapterReview" class="review-content">
            <div class="review-row">
              <n-text depth="3">状态</n-text>
              <n-tag size="small" round :type="getReviewStatusType(chapterReview.status)">
                {{ reviewStatusLabel(chapterReview.status) }}
              </n-tag>
            </div>
            <div v-if="chapterReview.memo" class="review-row">
              <n-text depth="3">备忘</n-text>
              <n-text class="memo-text">{{ chapterReview.memo }}</n-text>
            </div>
            <div v-if="chapterReview.updated_at" class="review-row">
              <n-text depth="3">更新</n-text>
              <n-text depth="3" style="font-size: 12px">{{ formatTime(chapterReview.updated_at) }}</n-text>
            </div>
          </div>
        </n-card>

        <!-- 正文结构 -->
        <n-card v-if="slug" size="small" :bordered="true" class="status-card">
          <template #header>
            <span class="card-title">📊 正文结构</span>
          </template>
          <n-empty v-if="!chapterStructure && !metaLoading" description="暂无结构分析" size="small" />
          <div v-else-if="chapterStructure" class="structure-grid">
            <div class="structure-item">
              <n-text depth="3">分段</n-text>
              <n-text class="structure-value">{{ chapterStructure.paragraph_count ?? '—' }}</n-text>
            </div>
            <div class="structure-item">
              <n-text depth="3">场景</n-text>
              <n-text class="structure-value">{{ chapterStructure.scene_count ?? '—' }}</n-text>
            </div>
            <div class="structure-item">
              <n-text depth="3">对白</n-text>
              <n-text class="structure-value">
                {{ chapterStructure.dialogue_ratio != null ? `${Math.round(chapterStructure.dialogue_ratio * 100)}%` : '—' }}
              </n-text>
            </div>
            <div class="structure-item">
              <n-text depth="3">节奏</n-text>
              <n-tag size="tiny" round>{{ pacingLabel(chapterStructure.pacing) }}</n-tag>
            </div>
          </div>
        </n-card>
      </n-spin>

      <!-- 全托管章末审阅 -->
      <n-card v-if="autopilotChapterReview" size="small" :bordered="true" class="status-card">
        <template #header>
          <span class="card-title">🤖 自动审阅</span>
        </template>
        <n-alert
          v-if="chapter && chapter.number !== autopilotChapterReview.chapter_number"
          type="info"
          size="small"
          style="margin-bottom: 12px"
        >
          为第 {{ autopilotChapterReview.chapter_number }} 章结果
        </n-alert>
        <div class="autopilot-review">
          <div class="review-row">
            <n-text depth="3">张力</n-text>
            <div class="tension-bar">
              <div class="tension-fill" :style="{ width: `${autopilotChapterReview.tension * 10}%` }"></div>
              <n-text class="tension-value">{{ autopilotChapterReview.tension }}/10</n-text>
            </div>
          </div>
          <div class="review-row">
            <n-text depth="3">管线</n-text>
            <n-tag :type="autopilotChapterReview.narrative_sync_ok ? 'success' : 'warning'" size="small" round>
              {{ autopilotChapterReview.narrative_sync_ok ? '已同步' : '同步失败' }}
            </n-tag>
          </div>
          <div v-if="autopilotChapterReview.similarity_score != null" class="review-row">
            <n-text depth="3">文风</n-text>
            <n-text>{{ Number(autopilotChapterReview.similarity_score).toFixed(3) }}</n-text>
          </div>
          <div class="review-row">
            <n-text depth="3">漂移</n-text>
            <n-tag :type="autopilotChapterReview.drift_alert ? 'error' : 'success'" size="small" round>
              {{ autopilotChapterReview.drift_alert ? '告警' : '正常' }}
            </n-tag>
          </div>
        </div>
      </n-card>

      <!-- AI 生成质检 -->
      <n-card v-if="lastWorkflowResult && qcChapterNumber != null" size="small" :bordered="true" class="status-card">
        <template #header>
          <span class="card-title">✨ 生成质检</span>
        </template>
        <n-space vertical :size="10">
          <n-alert
            v-if="chapter.number !== qcChapterNumber"
            type="info"
            size="small"
          >
            为第 {{ qcChapterNumber }} 章质检结果
          </n-alert>

          <ConsistencyReportPanel
            :report="lastWorkflowResult.consistency_report"
            :token-count="lastWorkflowResult.token_count"
            @location-click="onLocationClick"
          />

          <n-collapse
            v-if="lastWorkflowResult.style_warnings && lastWorkflowResult.style_warnings.length > 0"
            class="qc-collapse"
          >
            <n-collapse-item :title="`俗套句式 ${lastWorkflowResult.style_warnings.length} 处`" name="cliche">
              <n-space vertical :size="6">
                <n-alert
                  v-for="(w, i) in lastWorkflowResult.style_warnings"
                  :key="i"
                  :type="w.severity === 'warning' ? 'warning' : 'info'"
                  :title="w.pattern"
                  size="small"
                >
                  「{{ w.text }}」
                </n-alert>
              </n-space>
            </n-collapse-item>
          </n-collapse>

          <n-collapse v-if="ghostAnnotationLines.length > 0" class="qc-collapse">
            <n-collapse-item :title="`冲突批注 ${ghostAnnotationLines.length} 条`" name="ghost">
              <n-space vertical :size="6">
                <n-alert
                  v-for="(line, gi) in ghostAnnotationLines"
                  :key="gi"
                  type="warning"
                  size="small"
                >
                  {{ line }}
                </n-alert>
              </n-space>
            </n-collapse-item>
          </n-collapse>

          <n-space :size="8">
            <n-button size="tiny" quaternary @click="$emit('go-editor')">打开编辑</n-button>
            <n-button size="tiny" quaternary @click="$emit('clear-qc')">清除</n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-space>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { useMessage } from 'naive-ui'
import type { GenerateChapterWorkflowResponse } from '../../api/workflow'
import ConsistencyReportPanel from './ConsistencyReportPanel.vue'
import { chapterApi, type ChapterReviewDTO, type ChapterStructureDTO } from '../../api/chapter'

interface Chapter {
  id: number | string
  number: number
  title: string
  word_count: number
}

export interface AutopilotChapterAudit {
  chapter_number: number
  tension: number
  drift_alert: boolean
  similarity_score: number | null
  narrative_sync_ok: boolean
  at: string | null
}

const props = defineProps<{
  slug?: string
  chapter: Chapter | null
  readOnly?: boolean
  lastWorkflowResult?: GenerateChapterWorkflowResponse | null
  qcChapterNumber?: number | null
  autopilotChapterReview?: AutopilotChapterAudit | null
}>()

defineEmits<{
  (e: 'clear-qc'): void
  (e: 'go-editor'): void
}>()

const message = useMessage()

const metaLoading = ref(false)
const chapterReview = ref<ChapterReviewDTO | null>(null)
const chapterStructure = ref<ChapterStructureDTO | null>(null)

const ghostAnnotationLines = computed(() => {
  const raw = props.lastWorkflowResult?.ghost_annotations
  if (!raw || !Array.isArray(raw) || raw.length === 0) return []
  const lines: string[] = []
  for (const item of raw) {
    if (item == null) continue
    if (typeof item === 'string') {
      lines.push(item)
      continue
    }
    if (typeof item === 'object') {
      const o = item as Record<string, unknown>
      const msg =
        (typeof o.message === 'string' && o.message) ||
        (typeof o.summary === 'string' && o.summary) ||
        (typeof o.text === 'string' && o.text) ||
        JSON.stringify(o)
      lines.push(msg)
    }
  }
  return lines
})

function reviewStatusLabel(s: string) {
  const m: Record<string, string> = {
    draft: '草稿',
    reviewed: '已审',
    approved: '通过',
    pending: '待定',
  }
  return m[s] || s || '—'
}

function getReviewStatusType(s: string): 'default' | 'info' | 'success' | 'warning' | 'error' {
  const m: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
    draft: 'default',
    reviewed: 'info',
    approved: 'success',
    pending: 'warning',
  }
  return m[s] || 'default'
}

function pacingLabel(p: string) {
  const m: Record<string, string> = {
    slow: '慢',
    medium: '中',
    fast: '快',
  }
  return m[p] || p || '—'
}

function formatTime(t: string) {
  try {
    return new Date(t).toLocaleString('zh-CN', {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch {
    return t
  }
}

async function loadChapterMeta() {
  chapterReview.value = null
  chapterStructure.value = null
  if (!props.slug || !props.chapter) return
  metaLoading.value = true
  try {
    const [rev, struct] = await Promise.allSettled([
      chapterApi.getChapterReview(props.slug, props.chapter.number),
      chapterApi.getChapterStructure(props.slug, props.chapter.number),
    ])
    chapterReview.value = rev.status === 'fulfilled' ? rev.value : null
    chapterStructure.value = struct.status === 'fulfilled' ? struct.value : null
  } finally {
    metaLoading.value = false
  }
}

watch(
  () => [props.slug, props.chapter?.number] as const,
  () => {
    void loadChapterMeta()
  },
  { immediate: true }
)

function onLocationClick(location: number) {
  message.info(`问题位置约在第 ${location} 字附近`)
}
</script>

<style scoped>
.chapter-status-panel {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 16px 20px;
}

.status-card {
  transition: all 0.2s ease;
}

.status-card:hover {
  border-color: var(--n-primary-color-hover);
}

.card-title {
  font-size: 13px;
  font-weight: 600;
}

/* 章节头部 */
.chapter-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.chapter-title-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.chapter-number {
  font-size: 13px;
  font-weight: 600;
  color: var(--n-text-color-2);
}

.chapter-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--n-text-color-1);
}

.chapter-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.word-count {
  font-size: 12px;
}

/* 审阅内容 */
.review-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.review-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.memo-text {
  font-size: 12px;
  text-align: right;
  max-width: 60%;
}

/* 结构网格 */
.structure-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.structure-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.structure-value {
  font-size: 16px;
  font-weight: 600;
  color: var(--n-text-color-1);
}

/* 自动审阅 */
.autopilot-review {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tension-bar {
  position: relative;
  width: 100%;
  height: 20px;
  background: var(--n-color-modal);
  border-radius: 10px;
  overflow: hidden;
}

.tension-fill {
  height: 100%;
  background: linear-gradient(90deg, #10b981, #f59e0b, #ef4444);
  border-radius: 10px;
  transition: width 0.3s ease;
}

.tension-value {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 11px;
  font-weight: 600;
  color: var(--n-text-color-1);
}

/* 折叠面板 */
.qc-collapse :deep(.n-collapse-item__header) {
  font-size: 12px;
}
</style>
