<script setup>
import TaskCatalog from './TaskCatalog.vue';
import ReplayView from './ReplayView.vue';
import SceneManager from './SceneManager.vue';
import MapWorkspace from './MapWorkspace.vue';
const query=new URLSearchParams(location.search);
const legacy=query.has('run_id')||query.has('fixture');
</script>
<template><TaskCatalog/><template v-if="legacy"><div class="legacy-notice">独立旧运行 / 显式 fixture 回放 · 不归入新场景目录 <a href="/">进入时空地图</a></div><ReplayView/></template><SceneManager v-else-if="query.has('classic')"/><MapWorkspace v-else/></template>
