<script setup>
import TaskCatalog from './TaskCatalog.vue';
import ReplayView from './ReplayView.vue';
import MapWorkspace from './MapWorkspace.vue';
const query = new URLSearchParams(location.search);
const replay = query.has('run_id') || query.has('fixture');
</script>
<template><TaskCatalog /><ReplayView v-if="replay" /><MapWorkspace v-else /></template>
