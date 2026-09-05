# What we have from a level, and what we do not

Audit of `mpl_arena_a` -- the most complete package -- across three
questions that are usually collapsed into one.

| | count |
|---|--:|
| Resource types the level ships | **99** |
| Something in this tree parses | 96 |
| Reaches the Blender package | 17 |
| Both parsed AND exported | **17** |
| Parsed but never exported | 79 |
| Neither | 3 |

The middle row is the real number: 17 of 99 types are understood well enough
to reach Blender. The rest are the gap.

## Exported (the level's visible half)

| type | carried by |
|---|---|
| `CActorDataResourceWin10` | scripts.json / static_entities.json (actor ids) |
| `CGFSEffectsResourceWin10` | effects.json |
| `CGInstancedModelResourceWin10` | manifest.meshes + blobs/instances.bin |
| `CGLightMapResourceWin10` | lightmaps/ + lightmaps.json |
| `CGMaterialResourceWin10` | materials.json |
| `CGMeshListResourceWin10` | manifest.meshes + blobs/*_pos/idx/uv0 |
| `CGReflectionProbeResourceWin10` | probes/ + manifest.reflection_probes |
| `CGSceneResourceWin10` | lightmaps.json lights + volume_lights |
| `CGShaderSetResourceWin10` | materials.json (shaderset_hash only) |
| `CGStaticInstanceResourceWin10` | static_entities.json |
| `CGTextureResourceWin10` | textures/ |
| `CGTextureStreamingResourceWin10` | textures/ (mip selection) |
| `CParticleEffectCRWin10` | effects.json (emitters) |
| `CScriptCRWin10` | scripts.json |
| `CStaticInstanceModelCRWin10` | static_entities.json |
| `CTextureOverrideCRWin10` | texture_overrides.json |
| `CTransformCRWin10` | blobs/instances.bin |

## Parsed but NOT exported -- understood, invisible to Blender

| type | parsed by |
|---|---|
| `CActorLODCRWin10` | cactor_lod_cr.py, cactorlodcr.py |
| `CActorRegionLODCRWin10` | cactor_region_lod_cr.py, cactorregionlodcr.py |
| `CAmbientSoundCRWin10` | cambient_sound_cr.py, cambientsoundcr.py |
| `CAnimSetResourceWin10` | canimset_resource.py |
| `CAnimationCRWin10` | canimationcr.py |
| `CBVHResourceWin10` | cbvh_resource.py, cbvhresource.py |
| `CBillboardCRWin10` | cbillboard_cr.py, cbillboardcr.py |
| `CBoundingSphereCRWin10` | cboundingspherecr.py |
| `CCanvasUICRWin10` | ccanvas_ui_cr.py, ccanvasuicr.py |
| `CComponentLODCRWin10` | ccomponent_lod_cr.py, ccomponentlodcr.py |
| `CComponentRegionLODCRWin10` | ccomponent_region_lod_cr.py |
| `CComponentSpaceResourceWin10` | ccomponent_space_resource.py, ccomponentspaceresource.py |
| `CDialogue2CRWin10` | cdialogue2_cr.py, cdialogue2cr.py |
| `CDynamicLODRegionTargetCRWin10` | cdynamic_lod_region_target_cr.py, cdynamiclodregiontargetcr.py |
| `CEventCRWin10` | cevent_cr.py |
| `CFrustumCullCRWin10` | cfrustumcullcr.py |
| `CGParticleEffectResourceWin10` | cg_particle_effect_resource.py |
| `CGParticleGraphResourceWin10` | cg_particle_graph_resource.py |
| `CGVisibilityResourceWin10` | cg_visibility_resource.py, cgvisibilityresource.py |
| `CGameLevelInfoResourceWin10` | cgamelevelinforesource.py |
| `CGameLevelResourceWin10` | cgame_level_resource.py, cgamelevelresource.py |
| `CInputCRWin10` | cinput_cr.py, cinputcr.py |
| `CLODRegionCRWin10` | clod_region_cr.py |
| `CLegacyCameraDataCRWin10` | clegacy_camera_data_cr.py |
| `CLensConfigResourceWin10` | clens_config_resource.py |
| `CLensResourceWin10` | clens_resource.py |
| `CLevelAABBCRWin10` | clevelaabbcr.py |
| `CListCRWin10` | clist_cr.py |
| `CMaterialTypeCRWin10` | cmaterialtypecr.py |
| `CMaterialTypesBVHResourceWin10` | cmaterial_types_bvh_resource.py, cmaterialtypesbvhresource.py |
| `CModelCRWin10` | cmodelcr.py |
| `COccluderMeshCRWin10` | coccluder_mesh_cr.py, coccludermeshcr.py |
| `COccluderMeshResourceWin10` | coccludermeshresource.py |
| `COcclusionCullCRWin10` | cocclusioncullcr.py |
| `CPhysicsCRWin10` | cphysics_cr.py, cphysicscr.py |
| `CPhysicsResourceWin10` | cphysicsresource.py |
| `CPlatformCRWin10` | cplatform_cr.py |
| `CPositionSelectionCRWin10` | cposition_selection_cr.py |
| `CR15ButtonInteractCRWin10` | cr15_button_interact_cr.py, cr15buttoninteractcr.py |
| `CR15DockToViewCRWin10` | cr15_dock_to_view_cr.py, cr15docktoviewcr.py |
| `CR15FlagBaseCRWin10` | cr15flagbasecr.py |
| `CR15FlagCRWin10` | cr15_flag_cr.py, cr15flagcr.py |
| `CR15FrisbeeCRWin10` | cr15frisbeecr.py |
| `CR15HeraldryCRWin10` | cr15_heraldry_cr.py, cr15heraldrycr.py |
| `CR15NetAIWaypointCRWin10` | cr15_net_ai_waypoint_cr.py |
| `CR15NetActorCRWin10` | cr15_net_actor_cr.py, cr15netactorcr.py |
| `CR15NetBalanceSettingsCRWin10` | cr15_net_balance_settings_cr.py |
| `CR15NetCustomizationCRWin10` | cr15_net_customization_cr.py |
| `CR15NetFrisbeeTrailCRWin10` | cr15_net_frisbee_trail_cr.py, cr15netfrisbeetrailcr.py |
| `CR15NetHoloBitCRWin10` | cr15_net_holobit_cr.py, cr15netholobitcr.py |
| `CR15NetIdCRWin10` | cr15_net_id_cr.py, cr15netidcr.py |
| `CR15NetPhysicsCRWin10` | cr15_net_physics_cr.py, cr15netphysicscr.py |
| `CR15NetPooledActorCRWin10` | cr15_net_pooled_actor_cr.py, cr15netpooledactorcr.py |
| `CR15NetPunchableCRWin10` | cr15_net_punchable_cr.py, cr15netpunchablecr.py |
| `CR15NetRemoteVolumeQueryCRWin10` | cr15_net_remote_volume_query_cr.py, cr15netremotevolumequerycr.py |
| `CR15NetSpectatorCameraCRWin10` | cr15_net_spectator_camera_cr.py |
| `CR15NetTouchInteractCRWin10` | cr15_net_touch_interact_cr.py, cr15nettouchinteractcr.py |
| `CR15NetUISettingsCRWin10` | cr15_net_ui_settings_cr.py, cr15netuisettingscr.py |
| `CR15NetVarCRWin10` | cr15netvarcr.py |
| `CR15SpawnPointCRWin10` | cr15_spawn_point_cr.py, cr15spawnpointcr.py |
| `CR15TeamCRWin10` | cr15_team_cr.py, cr15teamcr.py |
| `CR15TouchInteractCRWin10` | cr15_touch_interact_cr.py, cr15touchinteractcr.py |
| `CR15UILayoutCRWin10` | cr15_ui_layout_cr.py |
| `CR15UIPageCRWin10` | cr15_ui_page_cr.py |
| `CResourceLoaderCRWin10` | cresource_loader_cr.py, cresourceloadercr.py |
| `CSVOPathPlannerCRWin10` | csvo_path_planner_cr.py, csvopathplannercr.py |
| `CSVOResourceWin10` | csvo_resource.py, csvoresource.py |
| `CSVOVolumeCRWin10` | csvo_volume_cr.py, csvovolumecr.py |
| `CScriptResourceWin10` | cscript_resource.py |
| `CSharedCanvasUICRWin10` | csharedcanvasuicr.py |
| `CSkeletonResourceWin10` | cskeleton_resource.py |
| `CSoundCRWin10` | csound_cr.py, csoundcr.py |
| `CStaticLODRegionTargetCRWin10` | cstatic_lod_region_target_cr.py, cstaticlodregiontargetcr.py |
| `CStaticRaycastCRWin10` | cstaticraycastcr.py |
| `CTTFontResourceWin10` | cttfontresource.py |
| `CTeamCRWin10` | cteam_cr.py, cteamcr.py |
| `CTextureStreamingCRWin10` | ctexture_streaming_cr.py, ctexturestreamingcr.py |
| `CUICanvasResourceWin10` | cui_canvas_resource.py, cuicanvasresource.py |
| `CWWiseSoundBankResourceWin10` | cwwise_sound_bank_resource.py |

## Neither parsed nor exported

* `CInstanceModelCRWin10` -- named in 16 source file(s), parsed by none
* `CR15SyncGrabCRWin10` -- named in 9 source file(s), parsed by none
* `CR15BounceCRWin10` -- named in 8 source file(s), parsed by none

## Package contents

Files: `blobs`, `effects.json`, `goal_explosion.json`, `lightmaps`, `lightmaps.json`, `manifest.json`, `materials.json`, `movers.json`, `probes`, `scripts.json`, `static_entities.json`, `texture_overrides.json`, `textures`, `ui`, `vertex_tints.json`

`manifest.json` keys: `axis`, `duplicate`, `format`, `instance_lightmap`, `instances_blob`, `lightmap_stats`, `lod`, `master`, `meshes`, `num_instances`, `num_meshes`, `reflection_probes`, `version`
